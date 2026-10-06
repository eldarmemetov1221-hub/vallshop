"""Платёжный провайдер PayHot — приём ₽ (карта / СБП / SberPay и др.).

Док: https://docs.pay.hot/overview

Создание платежа:
  POST {base}/payments   (Authorization: Bearer <api_key>, Idempotency-Key)
  тело: {merchant_order_reference, amount_minor (строка, копейки), currency,
         payment_method, customer?}
  ответ: {id, checkout:{url}, status}
Статус:
  GET {base}/payments/{id} -> {... status ...}
Вебхук (PayHot -> мерчант):
  заголовок ``PayHot-Signature: t=<unix>,v2=<64 hex>``;
  подпись = HMAC-SHA256( f"{t}.{raw_body}" , webhook_secret ), hex;
  допуск по времени ±5 минут; payload: {event_type, data:{id, status,
  merchant_order_reference, amount_minor, currency, payment_method}}.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from decimal import ROUND_HALF_UP, Decimal
from typing import List, Mapping, Optional

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from .base import Invoice, PaymentProvider, PaymentUpdate

log = logging.getLogger("vallshop.payhot")

# Статусы PayHot -> наши (pending | paid | failed | expired).
_PAID = {"succeeded", "captured"}
_FAILED = {"failed", "cancelled", "canceled", "refunded", "partially_refunded"}

_SIG_RE = re.compile(r"t=(\d{1,13}),v2=([a-f0-9]{64})")
_TOLERANCE_SEC = 300  # ±5 минут


def _norm_status(raw: Optional[str]) -> str:
    s = str(raw or "").strip().lower()
    if s in _PAID:
        return "paid"
    if s in _FAILED:
        return "failed"
    return "pending"


class PayHotProvider(PaymentProvider):
    name = "payhot"

    def __init__(
        self,
        *,
        base_url: str = "https://app.pay.hot/api/v2",
        api_key: Optional[str] = None,
        secret: Optional[str] = None,
        methods: Optional[List[str]] = None,
        timeout: float = 30.0,
        mock: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.secret = secret or ""
        self.methods = methods or ["card", "sbp", "sberpay"]
        self.timeout = timeout
        self.mock = mock

    # ── Транспорт ────────────────────────────────────────────────────────────
    def _headers(self, idempotency_key: Optional[str] = None) -> dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h

    @staticmethod
    def _unwrap(data):
        if isinstance(data, dict):
            for key in ("data", "payment"):
                if isinstance(data.get(key), dict):
                    return data[key]
        return data if isinstance(data, dict) else {}

    async def _request(self, method: str, path: str, *, json_body=None, idem=None) -> dict:
        if httpx is None:
            raise RuntimeError("Пакет 'httpx' не установлен")
        url = self.base_url + path
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.request(
                method, url,
                content=json.dumps(json_body).encode() if json_body is not None else None,
                headers=self._headers(idem),
            )
            if resp.status_code >= 400:
                log.error("PayHot %s %s -> HTTP %s: %s", method, path, resp.status_code, resp.text[:500])
                resp.raise_for_status()
            return resp.json()

    @staticmethod
    def _to_minor(amount: Decimal) -> str:
        return str(int((Decimal(amount) * 100).to_integral_value(rounding=ROUND_HALF_UP)))

    # ── Создание платежа ───────────────────────────────────────────────────────
    async def create_invoice(
        self,
        *,
        amount: Decimal,
        client_ref: str,
        description: str,
        notify_url: Optional[str] = None,
        success_url: Optional[str] = None,
        network: Optional[str] = None,  # здесь — способ оплаты (card/sbp/sberpay)
    ) -> Invoice:
        method = (network or (self.methods[0] if self.methods else "card")).lower()
        body = {
            "merchant_order_reference": client_ref,
            "amount_minor": self._to_minor(amount),
            "currency": "RUB",
            "payment_method": method,
        }
        if success_url:
            body["success_url"] = success_url

        if self.mock:
            d = {
                "id": f"ph-mock-{client_ref}",
                "checkout": {"url": (success_url or "https://pay.hot/mock") + f"?ref={client_ref}"},
                "status": "requires_payment_method",
            }
        else:
            d = self._unwrap(await self._request("POST", "/payments", json_body=body, idem=client_ref))

        checkout = d.get("checkout") if isinstance(d.get("checkout"), dict) else {}
        return Invoice(
            provider_order_id=str(d.get("id") or client_ref),
            checkout_url=(checkout.get("url") or d.get("checkout_url")),
            address=None,
            network=method,
            amount=Decimal(amount),
            currency="RUB",
            expires_at=None,
            raw=d,
        )

    async def get_status(self, ref: str) -> PaymentUpdate:
        """Статус платежа по id PayHot (мы храним его в topup.provider_order_id)."""
        if self.mock:
            return PaymentUpdate(provider_order_id=ref, status="paid", client_ref=None)
        d = self._unwrap(await self._request("GET", f"/payments/{ref}"))
        return PaymentUpdate(
            provider_order_id=str(d.get("id") or ref),
            status=_norm_status(d.get("status")),
            client_ref=d.get("merchant_order_reference"),
            amount=None,
            raw=d,
        )

    # ── Вебхук ────────────────────────────────────────────────────────────────
    def verify_webhook(self, raw_body: bytes, headers: Mapping[str, str]) -> bool:
        if self.mock:
            return True
        lower = {k.lower(): v for k, v in headers.items()}
        header = lower.get("payhot-signature") or ""
        m = _SIG_RE.search(header)
        if not m or not self.secret:
            return False
        ts, provided = m.group(1), m.group(2)
        # Защита от повторов/подделки времени.
        try:
            if abs(int(time.time()) - int(ts)) > _TOLERANCE_SEC:
                return False
        except ValueError:
            return False
        signed = f"{ts}.".encode("utf-8") + raw_body
        expected = hmac.new(self.secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
        return hmac.compare_digest(provided, expected)

    def parse_webhook(self, raw_body: bytes) -> PaymentUpdate:
        data = json.loads(raw_body.decode("utf-8"))
        d = data.get("data", {}) if isinstance(data, dict) else {}
        return PaymentUpdate(
            provider_order_id=str(d.get("id")) if d.get("id") else None,
            status=_norm_status(d.get("status")),
            client_ref=d.get("merchant_order_reference"),
            amount=None,
            raw=data,
        )
