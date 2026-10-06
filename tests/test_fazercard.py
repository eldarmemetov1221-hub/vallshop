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


@pytest.mark.parametrize("status", ["refund", "refunded", "returned", "reversed", "chargeback"])
def test_refund_statuses_are_terminal_failed(status):
    # Steam-пополнение/топапы возвращают деньги со статусом "refund" (без -ed).
    # Это должно трактоваться как провал заказа, иначе поллер зависнет навсегда
    # и покупателю не вернут баланс (реальный баг Steam-пополнения).
    order = {"order": {"status": status}}
    assert FazerCardClient.status_is_terminal_failed(order)
    assert not FazerCardClient.status_is_terminal_ok(order)


def test_mock_mode_order():
    c = FazerCardClient(mock=True)
    data = c.order_gamekey(game_id="g", key_id="k", quantity=3)
    assert data["ok"] is True
    assert FazerCardClient.extract_codes(data) == ["MOCK-1", "MOCK-2", "MOCK-3"]
    assert c.balance()["currency"] == "USD"


def test_offers_for_normalizes_giftcard_and_gamekey():
    c = FazerCardClient(mock=True)
    gc = c.offers_for("giftcard", "amazon_us")
    assert gc[0]["id"] == "gc10" and gc[0]["price_usd"] == "8.50"
    gk = c.offers_for("gamekey", "steam")
    assert gk[0]["id"] == "k1" and gk[0]["name"] == "Standard Edition"
    tu = c.offers_for("topup", "pubg")
    assert tu[0]["id"] == "o1"
    # Поля игрока — на уровне категории (topup_meta), а не оффера.
    meta = c.topup_meta("pubg")
    assert meta["fields"][0]["key"] == "player_id"
    assert meta["fields"][1]["type"] == "select"


def test_all_categories_follows_items():
    c = FazerCardClient(mock=True)
    cats = c.all_categories("giftcard")
    ids = {x["id"] for x in cats}
    assert "amazon_us" in ids and "steam" in ids


def test_fazercard_stock_helper_maps_live_stock():
    import asyncio
    from types import SimpleNamespace

    from bot.services.catalog import fazercard_stock

    c = FazerCardClient(mock=True)
    variants = [
        SimpleNamespace(id=1, source="fazercard", fzr_kind="giftcard", fzr_a="amazon_us", fzr_b="gc10"),
        SimpleNamespace(id=2, source="fazercard", fzr_kind="giftcard", fzr_a="amazon_us", fzr_b="gc25"),
        SimpleNamespace(id=3, source="stock", fzr_kind=None, fzr_a=None, fzr_b=None),
    ]
    out = asyncio.run(fazercard_stock(c, variants))
    assert out[1] == 5 and out[2] == 2
    assert 3 not in out  # не-FazerCard номиналы не трогаем


def test_cards_endpoint_sends_category_param():
    sess = _Session(_Resp(200, {"ok": True, "offers": [{"card_id": "c1", "name": "n", "price_usd": "1.00"}]}))
    c = FazerCardClient(api_key="fc_test", session=sess)
    c.list_giftcard_cards(category_id="amazon_us")
    assert sess.calls[0]["params"]["category_id"] == "amazon_us"
    assert sess.calls[0]["url"].endswith("/api/v2/giftcards/cards")
