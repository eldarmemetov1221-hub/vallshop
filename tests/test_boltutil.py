"""Тесты BoltUtil: детерминированная подпись и парсинг/проверка вебхука."""

import hashlib
import hmac
import json

import pytest

from bot.payments.boltutil import BoltUtilProvider

SECRET = "whsec_test_123"


@pytest.fixture
def provider():
    return BoltUtilProvider(
        base_url="https://boltutil.com",
        api_key="bt_live_test",
        secret=SECRET,
        network="TRC20",
    )


def _reference_sign(ts: str, body: str, secret: str = SECRET) -> str:
    payload = f"{ts}.{body}".encode("utf-8")
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def test_sign_matches_documented_scheme(provider):
    ts = "1739570000000"
    body = '{"id":"123"}'
    assert provider.sign(ts, body) == _reference_sign(ts, body)


def test_sign_is_lowercase_hex(provider):
    sig = provider.sign("1739570000000", '{"a":1}')
    assert sig == sig.lower()
    assert len(sig) == 64
    int(sig, 16)  # валидный hex


def test_sign_deterministic(provider):
    ts, body = "1700000000000", '{"externalOrderId":"vs-abc"}'
    assert provider.sign(ts, body) == provider.sign(ts, body)


def test_verify_webhook_accepts_valid_signature(provider):
    raw = json.dumps(
        {"status": "CONFIRMED", "externalOrderId": "vs-1", "txHash": "0xabc"},
        separators=(",", ":"),
    ).encode()
    ts = "1739570000000"
    sig = hmac.new(
        SECRET.encode(), f"{ts}.".encode() + raw, hashlib.sha256
    ).hexdigest()
    headers = {
        "X-Bolt-Webhook-Timestamp": ts,
        "X-Bolt-Webhook-Signature": sig,
    }
    assert provider.verify_webhook(raw, headers) is True


def test_verify_webhook_rejects_tampered_body(provider):
    raw = b'{"status":"CONFIRMED","externalOrderId":"vs-1"}'
    ts = "1739570000000"
    sig = hmac.new(
        SECRET.encode(), f"{ts}.".encode() + raw, hashlib.sha256
    ).hexdigest()
    headers = {"X-Bolt-Webhook-Timestamp": ts, "X-Bolt-Webhook-Signature": sig}
    tampered = b'{"status":"CONFIRMED","externalOrderId":"vs-HACK"}'
    assert provider.verify_webhook(tampered, headers) is False


def test_verify_webhook_rejects_missing_headers(provider):
    assert provider.verify_webhook(b"{}", {}) is False


def test_parse_webhook_maps_confirmed_to_paid(provider):
    raw = json.dumps(
        {
            "status": "CONFIRMED",
            "amount": "13.64",
            "externalOrderId": "vs-xyz",
            "txHash": "0xdeadbeef",
        }
    ).encode()
    upd = provider.parse_webhook(raw)
    assert upd.status == "paid"
    assert upd.client_ref == "vs-xyz"
    assert upd.tx_hash == "0xdeadbeef"
    assert str(upd.amount) == "13.64"


def test_parse_webhook_pending_and_expired(provider):
    assert provider.parse_webhook(b'{"status":"PENDING"}').status == "pending"
    assert provider.parse_webhook(b'{"status":"EXPIRED"}').status == "expired"
