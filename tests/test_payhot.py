"""PayHot: подпись вебхука, парсинг, создание счёта (mock), маппинг статусов."""

import hashlib
import hmac
import json
import time
from decimal import Decimal

import pytest

from bot.payments.payhot import PayHotProvider, _norm_status


SECRET = "whsec_test_123"


def _sign(raw: bytes, ts: int) -> str:
    mac = hmac.new(SECRET.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return f"t={ts},v2={mac}"


def test_status_map():
    assert _norm_status("succeeded") == "paid"
    assert _norm_status("captured") == "paid"
    assert _norm_status("failed") == "failed"
    assert _norm_status("cancelled") == "failed"
    assert _norm_status("refunded") == "failed"
    assert _norm_status("processing") == "pending"
    assert _norm_status("requires_payment_method") == "pending"


def test_verify_webhook_ok_and_bad():
    p = PayHotProvider(secret=SECRET)
    raw = b'{"event_type":"payment.succeeded","data":{"id":"x","status":"succeeded"}}'
    ts = int(time.time())
    good = {"PayHot-Signature": _sign(raw, ts)}
    assert p.verify_webhook(raw, good) is True
    # подделка тела
    assert p.verify_webhook(raw + b" ", good) is False
    # неверная подпись
    assert p.verify_webhook(raw, {"PayHot-Signature": f"t={ts},v2={'0'*64}"}) is False
    # нет заголовка
    assert p.verify_webhook(raw, {}) is False
    # просроченное время (> 5 мин)
    old = {"PayHot-Signature": _sign(raw, ts - 1000)}
    assert p.verify_webhook(raw, old) is False


def test_parse_webhook():
    p = PayHotProvider(secret=SECRET)
    body = {
        "event_type": "payment.succeeded",
        "data": {
            "id": "pay-123",
            "status": "succeeded",
            "merchant_order_reference": "tu-abc",
            "amount_minor": "10000",
            "currency": "RUB",
        },
    }
    upd = p.parse_webhook(json.dumps(body).encode())
    assert upd.status == "paid"
    assert upd.client_ref == "tu-abc"
    assert upd.provider_order_id == "pay-123"


@pytest.mark.asyncio
async def test_create_invoice_mock():
    p = PayHotProvider(secret=SECRET, mock=True, methods=["card", "sbp"])
    inv = await p.create_invoice(
        amount=Decimal("500"), client_ref="tu-xyz",
        description="top-up", success_url="https://shop.vallshop.com", network="sbp",
    )
    assert inv.currency == "RUB"
    assert inv.provider_order_id
    assert inv.checkout_url
    assert inv.network == "sbp"
    assert inv.amount == Decimal("500")


@pytest.mark.asyncio
async def test_get_status_mock_paid():
    p = PayHotProvider(secret=SECRET, mock=True)
    upd = await p.get_status("ph-mock-tu-xyz")
    assert upd.status == "paid"


def test_to_minor():
    assert PayHotProvider._to_minor(Decimal("500")) == "50000"
    assert PayHotProvider._to_minor(Decimal("78.40")) == "7840"
