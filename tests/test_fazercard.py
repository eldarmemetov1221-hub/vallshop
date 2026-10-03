"""Тесты клиента FazerCard (без реальной сети)."""

import json

import pytest

from fazercard import FazerCardAPIError, FazerCardClient


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _Session:
    """Фейковая сессия requests: записывает вызов и отдаёт заготовленный ответ."""

    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def request(self, method, url, params=None, data=None, headers=None, timeout=None):
        self.calls.append(
            {"method": method, "url": url, "params": params, "data": data, "headers": headers}
        )
        return self.resp


def test_balance_sends_api_key_header():
    sess = _Session(_Resp(200, {"ok": True, "balance": "12.50", "currency": "USD"}))
    c = FazerCardClient(api_key="fc_test", session=sess)
    data = c.balance()
    assert data["balance"] == "12.50"
    assert sess.calls[0]["headers"]["X-API-Key"] == "fc_test"
    assert sess.calls[0]["url"].endswith("/api/v2/balance")


def test_non_ok_raises():
    sess = _Session(_Resp(400, {"ok": False, "error": "bad", "code": "X"}))
    c = FazerCardClient(api_key="fc_test", session=sess)
    with pytest.raises(FazerCardAPIError):
        c.balance()


def test_order_giftcard_payload_and_idempotency():
    sess = _Session(_Resp(200, {"ok": True, "order": {"id": "o1", "status": "completed", "cards": ["AAA", "BBB"]}}))
    c = FazerCardClient(api_key="fc_test", session=sess)
    data = c.order_giftcard(category_id="cat", card_id="card", quantity=2, idempotency_key="k1")
    body = json.loads(sess.calls[0]["data"].decode())
    assert body == {"category_id": "cat", "card_id": "card", "quantity": 2}
    assert sess.calls[0]["headers"]["Idempotency-Key"] == "k1"
    assert FazerCardClient.extract_codes(data) == ["AAA", "BBB"]


def test_extract_codes_from_keys_objects():
    order = {"order": {"status": "completed", "keys": [{"key": "K1"}, {"code": "K2"}]}}
    assert FazerCardClient.extract_codes(order) == ["K1", "K2"]


def test_status_helpers():
    assert FazerCardClient.status_is_terminal_ok({"order": {"status": "completed"}})
    assert FazerCardClient.status_is_terminal_failed({"order": {"status": "refunded"}})
    assert not FazerCardClient.status_is_terminal_ok({"order": {"status": "pending"}})


def test_mock_mode_order():
    c = FazerCardClient(mock=True)
    data = c.order_gamekey(game_id="g", key_id="k", quantity=3)
    assert data["ok"] is True
    assert FazerCardClient.extract_codes(data) == ["MOCK-1", "MOCK-2", "MOCK-3"]
    assert c.balance()["currency"] == "USD"
