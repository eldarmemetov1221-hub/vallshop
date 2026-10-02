"""Платёжный провайдер BoltUtil — некастодиальный USDT-шлюз.

Реализация по официальной документации boltutil.com/ru/developer-docs.

Подпись (для ВСЕХ запросов и колбэков):
  * алгоритм: HMAC-SHA256;
  * ключ: ваш Webhook Secret (один и тот же везде);
  * строка для подписи: "{timestamp_ms}.{raw_body}" (Unix-время в мс, точка,
    затем РОВНО то сырое тело JSON, которое уходит в сеть);
  * кодировка подписи: hex в нижнем регистре.

Заголовки исходящего запроса (мерчант -> BoltUtil):
  X-Bolt-Key, X-Bolt-Timestamp, X-Bolt-Signature
Заголовки входящего колбэка (BoltUtil -> мерчант):
  X-Bolt-Webhook-Timestamp, X-Bolt-Webhook-Signature

Важно:
  * песочницы нет — только продакшн;
  * минимальная сумма платежа 1.00 USDT;
  * платить нужно payment.amount из ответа (может быть чуть выше запрошенной
    суммы для автосверки) — не округлять;
  * сверка и выдача — по externalOrderId (наш client_ref) и статусу CONFIRMED.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Mapping, Optional

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from .base import Invoice, PaymentProvider, PaymentUpdate

log = logging.getLogger("vallshop.boltutil")

MIN_PAYMENT_USDT = Decimal("1.00")

# Статусы жизненного цикла заказа BoltUtil -> наши.
_STATUS_MAP = {
    "pending": "pending",
    "confirmed": "paid",
    "expired": "expired",
}


def _norm_status(raw: Optional[str]) -> str:
    return _STATUS_MAP.get(str(raw or "").strip().lower(), "pending")


def _ts_ms() -> str:
    return str(int(time.time() * 1000))


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        # expireTimestamp — Unix-мс; ISO8601 — строкой.
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        s = str(value)
        if s.isdigit():
            return datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None


class BoltUtilProvider(PaymentProvider):
    name = "boltutil"

    CREATE_PATH = "/api/v1/order/create"
    STATUS_PATH = "/api/v1/order/status"

    API_KEY_HEADER = "X-Bolt-Key"
    REQ_TS_HEADER = "X-Bolt-Timestamp"
    REQ_SIGN_HEADER = "X-Bolt-Signature"
    WEBHOOK_TS_HEADER = "X-Bolt-Webhook-Timestamp"
    WEBHOOK_SIGN_HEADER = "X-Bolt-Webhook-Signature"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        secret: str,
        network: str = "TRC20",
        timeout: float = 30.0,
        expire_minutes: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.secret = secret  # Webhook Secret — ключ HMAC для всего
        self.network = network
        self.timeout = timeout
        self.expire_minutes = expire_minutes

    # ── Подпись ────────────────────────────────────────────────────────────
    def sign(self, timestamp: str, body: str) -> str:
        """HMAC-SHA256 от "{timestamp}.{body}", hex в нижнем регистре."""
        payload = f"{timestamp}.{body}".encode("utf-8")
        return hmac.new(self.secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _serialize(payload: Mapping) -> str:
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)

    def _request_headers(self, body: str) -> dict:
        ts = _ts_ms()
        return {
            "Content-Type": "application/json",
            self.API_KEY_HEADER: self.api_key,
            self.REQ_TS_HEADER: ts,
            self.REQ_SIGN_HEADER: self.sign(ts, body),
        }

    # ── API ──────────────────────────────────────────────────────────────
    async def _post(self, path: str, payload: Mapping) -> dict:
        if httpx is None:
            raise RuntimeError("Пакет 'httpx' не установлен")
        body = self._serialize(payload)
        headers = self._request_headers(body)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                self.base_url + path, content=body.encode("utf-8"), headers=headers
            )
            if resp.status_code >= 400:
                # Логируем причину от BoltUtil (без секретов) — для диагностики.
                log.error(
                    "BoltUtil %s -> HTTP %s: %s",
                    path,
                    resp.status_code,
                    resp.text[:500],
                )
                resp.raise_for_status()
            data = resp.json()
        # Ответ может быть «плоским» или в конверте {data:{...}}.
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            return data["data"]
        return data if isinstance(data, dict) else {}

    async def create_invoice(
        self,
        *,
        amount: Decimal,
        client_ref: str,
        description: str,
        notify_url: Optional[str] = None,
        success_url: Optional[str] = None,
    ) -> Invoice:
        if Decimal(amount) < MIN_PAYMENT_USDT:
            raise ValueError(
                f"Минимальная сумма платежа BoltUtil — {MIN_PAYMENT_USDT} USDT"
            )

        payload = {
            "amount": f"{Decimal(amount):.6f}".rstrip("0").rstrip("."),
            "currency": "USDT",
            "network": self.network,
            "externalOrderId": client_ref,
            "orderDesc": description or client_ref,
        }
        if notify_url:
            payload["notifyUrl"] = notify_url
        if success_url:
            payload["returnUrl"] = success_url
        if self.expire_minutes:
            payload["expiredMinutes"] = self.expire_minutes

        d = await self._post(self.CREATE_PATH, payload)
        pay = d.get("payment", {}) if isinstance(d.get("payment"), dict) else {}

        return Invoice(
            provider_order_id=str(d.get("orderToken") or client_ref),
            checkout_url=d.get("checkoutUrl"),
            address=pay.get("address"),
            network=pay.get("network") or self.network,
            # Платить нужно именно payment.amount (может быть чуть выше запроса).
            amount=Decimal(str(pay.get("amount", amount))),
            currency=pay.get("currency") or "USDT",
            expires_at=_parse_dt(d.get("expireTimestamp"))
            or (datetime.utcnow() + timedelta(minutes=self.expire_minutes)),
            raw=d,
        )

    async def get_status(self, ref: str) -> PaymentUpdate:
        """Статус заказа по externalOrderId (= наш client_ref)."""
        d = await self._post(self.STATUS_PATH, {"externalOrderId": ref})
        return PaymentUpdate(
            provider_order_id=d.get("orderToken"),
            status=_norm_status(d.get("status")),
            client_ref=d.get("externalOrderId") or ref,
            tx_hash=d.get("txHash"),
            amount=Decimal(str(d["amount"])) if d.get("amount") is not None else None,
            raw=d,
        )

    # ── Вебхук ────────────────────────────────────────────────────────────
    def verify_webhook(self, raw_body: bytes, headers: Mapping[str, str]) -> bool:
        lower = {k.lower(): v for k, v in headers.items()}
        ts = lower.get(self.WEBHOOK_TS_HEADER.lower())
        provided = lower.get(self.WEBHOOK_SIGN_HEADER.lower())
        if not ts or not provided:
            return False
        payload = f"{ts}.".encode("utf-8") + raw_body
        expected = hmac.new(
            self.secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(str(provided).strip().lower(), expected)

    def parse_webhook(self, raw_body: bytes) -> PaymentUpdate:
        data = json.loads(raw_body.decode("utf-8"))
        d = data.get("data", data) if isinstance(data, dict) else {}
        return PaymentUpdate(
            provider_order_id=d.get("orderToken"),
            status=_norm_status(d.get("status")),
            client_ref=d.get("externalOrderId"),
            tx_hash=d.get("txHash"),
            amount=Decimal(str(d["amount"])) if d.get("amount") is not None else None,
            raw=data,
        )
