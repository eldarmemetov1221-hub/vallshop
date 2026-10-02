"""Mock-платёжка для локальной разработки (без сети и без реальных денег).

Счёт сразу считается «оплаченным» при опросе статуса, подпись вебхука всегда
валидна. Включается через MOCK_PAYMENTS=1. Никогда не использовать в проде.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Mapping, Optional

from .base import Invoice, PaymentProvider, PaymentUpdate


class MockProvider(PaymentProvider):
    name = "mock"

    def __init__(self, *, network: str = "TRC20", expire_minutes: int = 30) -> None:
        self.network = network
        self.expire_minutes = expire_minutes

    async def create_invoice(
        self,
        *,
        amount: Decimal,
        client_ref: str,
        description: str,
        notify_url: Optional[str] = None,
        success_url: Optional[str] = None,
        network: Optional[str] = None,
    ) -> Invoice:
        oid = "mock-" + hashlib.sha256(client_ref.encode()).hexdigest()[:12]
        return Invoice(
            provider_order_id=oid,
            checkout_url=f"https://example.invalid/pay/{oid}",
            address="TMockAddressXXXXXXXXXXXXXXXXXXXXXX",
            network=(network or self.network),
            amount=amount,
            currency="USDT",
            expires_at=datetime.utcnow() + timedelta(minutes=self.expire_minutes),
            raw={"mock": True},
        )

    async def get_status(self, ref: str) -> PaymentUpdate:
        # В mock-режиме сразу «оплачено».
        return PaymentUpdate(
            provider_order_id=ref,
            status="paid",
            client_ref=ref,
            tx_hash="mocktx-" + ref,
            raw={"mock": True},
        )

    def verify_webhook(self, raw_body: bytes, headers: Mapping[str, str]) -> bool:
        return True

    def parse_webhook(self, raw_body: bytes) -> PaymentUpdate:
        data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        d = data.get("data", data) if isinstance(data, dict) else {}
        return PaymentUpdate(
            provider_order_id=str(d.get("orderToken") or ""),
            status="paid",
            client_ref=d.get("externalOrderId"),
            tx_hash=d.get("txHash"),
            raw=data,
        )
