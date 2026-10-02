"""Платёжный провайдер BoltUtil — некастодиальный USDT-шлюз.

BoltUtil: оплата в USDT (TRC20/ERC20/BEP20/Polygon/Solana) напрямую на
кошелёк продавца. API создаёт заказ и возвращает checkoutUrl; о платеже
сообщает подписанный (HMAC) вебхук на notifyUrl с txHash.

⚠️  ВАЖНО — СВЕРИТЬ С ДОКУМЕНТАЦИЕЙ КАБИНЕТА (boltutil.com/ru/developer-docs):
    Точные имена полей запроса/ответа и ТОЧНАЯ схема подписи (какая строка
    подписывается, hex или base64, имя заголовка) не были надёжно получены
    из публичной страницы (она рендерится через JS). Ниже — реализация по
    задокументированной форме. Места, требующие подтверждения, помечены
    комментарием «СВЕРИТЬ». Поведение вынесено в атрибуты класса, чтобы
    поправить их можно было в одном месте без переписывания логики.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Mapping, Optional

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from .base import Invoice, PaymentProvider, PaymentUpdate


# Нормализация статусов провайдера -> наши.  СВЕРИТЬ названия статусов.
_STATUS_MAP = {
    "pending": "pending",
    "new": "pending",
    "waiting": "pending",
    "created": "pending",
    "paid": "paid",
    "completed": "paid",
    "complete": "paid",
    "confirmed": "paid",
    "success": "paid",
    "underpaid": "underpaid",
    "partial": "underpaid",
    "expired": "expired",
    "timeout": "expired",
    "failed": "failed",
    "error": "failed",
    "cancelled": "failed",
}


def _norm_status(raw: Optional[str]) -> str:
    return _STATUS_MAP.get(str(raw or "").strip().lower(), "pending")


class BoltUtilProvider(PaymentProvider):
    name = "boltutil"

    # ── Параметры протокола (СВЕРИТЬ с документацией) ──────────────────────
    CREATE_PATH = "/api/v1/order/create"
    STATUS_PATH = "/api/v1/order/status"      # СВЕРИТЬ путь
    API_KEY_HEADER = "X-Bolt-Key"
    SIGN_HEADER = "X-Bolt-Sign"               # СВЕРИТЬ имя заголовка подписи
    WEBHOOK_SIGN_HEADER = "X-Bolt-Sign"       # СВЕРИТЬ имя заголовка в вебхуке
    SIGN_ENCODING = "hex"                      # "hex" | "base64"  (СВЕРИТЬ)

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        secret: str,
        webhook_secret: Optional[str] = None,
        network: str = "TRC20",
        timeout: float = 30.0,
        expire_minutes: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.secret = secret
        # Вебхук может подписываться отдельным секретом; по умолчанию — тем же.
        self.webhook_secret = webhook_secret or secret
        self.network = network
        self.timeout = timeout
        self.expire_minutes = expire_minutes

    # ── Подпись ────────────────────────────────────────────────────────────
    def _digest(self, raw: bytes, secret: str) -> str:
        mac = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256)
        if self.SIGN_ENCODING == "base64":
            return base64.b64encode(mac.digest()).decode("ascii")
        return mac.hexdigest()

    @staticmethod
    def _serialize(payload: Mapping) -> str:
        # Компактный JSON; подписываем ровно эти байты и их же отправляем.
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)

    # ── API ──────────────────────────────────────────────────────────────
    async def create_invoice(
        self,
        *,
        amount: Decimal,
        client_ref: str,
        description: str,
        notify_url: Optional[str] = None,
        success_url: Optional[str] = None,
    ) -> Invoice:
        if httpx is None:
            raise RuntimeError("Пакет 'httpx' не установлен")

        # СВЕРИТЬ имена полей тела запроса с документацией BoltUtil.
        payload = {
            "amount": str(amount),
            "currency": "USDT",
            "network": self.network,
            "orderId": client_ref,     # наш client_ref как внешний id заказа
            "description": description,
        }
        if notify_url:
            payload["notifyUrl"] = notify_url
        if success_url:
            payload["successUrl"] = success_url

        body = self._serialize(payload)
        headers = {
            "Content-Type": "application/json",
            self.API_KEY_HEADER: self.api_key,
            self.SIGN_HEADER: self._digest(body.encode("utf-8"), self.secret),
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                self.base_url + self.CREATE_PATH,
                content=body.encode("utf-8"),
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        # СВЕРИТЬ имена полей ответа.
        d = data.get("data", data) if isinstance(data, dict) else {}
        expires_at = datetime.utcnow() + timedelta(minutes=self.expire_minutes)
        return Invoice(
            provider_order_id=str(
                d.get("id") or d.get("orderId") or d.get("order_id") or client_ref
            ),
            checkout_url=d.get("checkoutUrl") or d.get("checkout_url"),
            address=d.get("address") or d.get("wallet"),
            network=d.get("network") or self.network,
            amount=Decimal(str(d.get("amount", amount))),
            currency="USDT",
            expires_at=expires_at,
            raw=data,
        )

    async def get_status(self, provider_order_id: str) -> PaymentUpdate:
        if httpx is None:
            raise RuntimeError("Пакет 'httpx' не установлен")

        payload = {"orderId": provider_order_id}  # СВЕРИТЬ
        body = self._serialize(payload)
        headers = {
            "Content-Type": "application/json",
            self.API_KEY_HEADER: self.api_key,
            self.SIGN_HEADER: self._digest(body.encode("utf-8"), self.secret),
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                self.base_url + self.STATUS_PATH,
                content=body.encode("utf-8"),
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        d = data.get("data", data) if isinstance(data, dict) else {}
        return PaymentUpdate(
            provider_order_id=str(d.get("id") or d.get("orderId") or provider_order_id),
            status=_norm_status(d.get("status")),
            client_ref=d.get("orderId") or d.get("clientRef"),
            tx_hash=d.get("txHash") or d.get("tx_hash"),
            amount=Decimal(str(d["amount"])) if d.get("amount") is not None else None,
            raw=data,
        )

    # ── Вебхук ────────────────────────────────────────────────────────────
    def verify_webhook(self, raw_body: bytes, headers: Mapping[str, str]) -> bool:
        # Заголовки приходят в разном регистре — нормализуем.
        lower = {k.lower(): v for k, v in headers.items()}
        provided = lower.get(self.WEBHOOK_SIGN_HEADER.lower())
        if not provided:
            return False
        expected = self._digest(raw_body, self.webhook_secret)
        return hmac.compare_digest(str(provided).strip(), expected)

    def parse_webhook(self, raw_body: bytes) -> PaymentUpdate:
        data = json.loads(raw_body.decode("utf-8"))
        d = data.get("data", data) if isinstance(data, dict) else {}
        return PaymentUpdate(
            provider_order_id=str(d.get("id") or d.get("orderId") or ""),
            status=_norm_status(d.get("status")),
            client_ref=d.get("orderId") or d.get("clientRef"),
            tx_hash=d.get("txHash") or d.get("tx_hash"),
            amount=Decimal(str(d["amount"])) if d.get("amount") is not None else None,
            raw=data,
        )
