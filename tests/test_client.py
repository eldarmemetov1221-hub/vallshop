"""Базовые тесты клиента LioGames (без сети)."""

import hashlib
import hmac

import pytest

from liogames import LioGamesClient, LioGamesConfigError


# Реальный completed-ответ из LIOGAMES_API.md (проверено на боевом аккаунте).
REAL_COMPLETED = {
    "ok": True,
    "code": "ORDER_STATUS",
    "data": {
        "order_id": 536857,
        "order_number": "575551",
        "status": "completed",
        "result": "SUCCESS",
        "is_paid": True,
        "total": 0.88,
        "items": [
            {
                "product_id": 66599,
                "variation_id": 534124,
                "name": "PUBG Mobile Code (Global) - 60 UC",
                "qty": 1,
            }
        ],
        "sn": "aYVQtqZs2E27YdH38d",
        "delivery_code": "aYVQtqZs2E27YdH38d",
        "delivery": {"ready": True, "codes": ["aYVQtqZs2E27YdH38d"]},
    },
}

VARIATIONS = (
    "60:534124,325:534125,660:534126,1800:534127,3850:534128,"
    "8100:534129,16200:534130,24300:534131,32400:534132,40500:534133"
)


@pytest.fixture
def client():
    return LioGamesClient(
        member_code="M2609247OQFNWJJ8W",
        secret="test-secret",
        product_id=66599,
        variations=VARIATIONS,
        mock=True,
    )


# ── Подпись ──────────────────────────────────────────────────────────────────

def test_serialize_body_is_compact_no_spaces():
    body = LioGamesClient.serialize_body({"member_code": "M1", "product_id": 66599})
    assert body == '{"member_code":"M1","product_id":66599}'
    assert " " not in body


def test_serialize_body_does_not_escape_slashes():
    body = LioGamesClient.serialize_body({"url": "a/b/c"})
    assert "a/b/c" in body
    assert "\\/" not in body


def test_serialize_body_escapes_non_ascii():
    # ensure_ascii=True -> не-ASCII как \uXXXX (совместимо с PHP json_encode).
    body = LioGamesClient.serialize_body({"name": "кириллица"})
    assert "\\u" in body
    assert "кириллица" not in body


def test_sign_is_deterministic(client):
    payload = {"member_code": "M2609247OQFNWJJ8W"}
    body = client.serialize_body(payload)
    first = client.sign(body)
    second = client.sign(body)
    assert first == second


def test_sign_matches_reference_hmac(client):
    body = client.serialize_body({"member_code": "M2609247OQFNWJJ8W"})
    expected = hmac.new(b"test-secret", body.encode(), hashlib.sha256).hexdigest()
    assert client.sign(body) == expected


def test_sign_without_secret_raises():
    c = LioGamesClient(member_code="M1", mock=True)
    with pytest.raises(LioGamesConfigError):
        c.sign("{}")


# ── extract_code ─────────────────────────────────────────────────────────────

def test_extract_code_from_real_completed_envelope():
    # Принимает весь конверт.
    assert LioGamesClient.extract_code(REAL_COMPLETED) == "aYVQtqZs2E27YdH38d"


def test_extract_code_from_data_only():
    assert LioGamesClient.extract_code(REAL_COMPLETED["data"]) == "aYVQtqZs2E27YdH38d"


def test_extract_code_priority_sn_first():
    data = {"sn": "AAA", "delivery_code": "BBB", "delivery": {"codes": ["CCC"]}}
    assert LioGamesClient.extract_code(data) == "AAA"


def test_extract_code_falls_back_to_delivery_code():
    data = {"delivery_code": "BBB", "delivery": {"codes": ["CCC"]}}
    assert LioGamesClient.extract_code(data) == "BBB"


def test_extract_code_falls_back_to_delivery_codes_list():
    data = {"delivery": {"ready": True, "codes": ["CCC"]}}
    assert LioGamesClient.extract_code(data) == "CCC"


def test_extract_code_none_when_missing():
    assert LioGamesClient.extract_code({"status": "processing"}) is None


# ── Статусы ──────────────────────────────────────────────────────────────────

def test_status_terminal_ok_on_real_completed():
    assert LioGamesClient.status_is_terminal_ok(REAL_COMPLETED) is True
    assert LioGamesClient.status_is_terminal_failed(REAL_COMPLETED) is False


@pytest.mark.parametrize("status", ["completed", "delivered", "success", "done"])
def test_status_ok_variants(status):
    assert LioGamesClient.status_is_terminal_ok({"status": status}) is True


@pytest.mark.parametrize("status", ["failed", "cancelled", "refunded", "expired"])
def test_status_failed_variants(status):
    assert LioGamesClient.status_is_terminal_failed({"status": status}) is True


def test_status_processing_is_not_terminal():
    data = {"status": "processing"}
    assert LioGamesClient.status_is_terminal_ok(data) is False
    assert LioGamesClient.status_is_terminal_failed(data) is False
    assert LioGamesClient.status_is_terminal(data) is False


def test_status_uses_result_field_when_no_status():
    assert LioGamesClient.status_is_terminal_ok({"result": "SUCCESS"}) is True


# ── Разрешение вариаций ──────────────────────────────────────────────────────

def test_resolve_variation_id(client):
    assert client.resolve_variation_id(60) == 534124
    assert client.resolve_variation_id("325") == 534125
    assert client.resolve_variation_id(40500) == 534133


def test_resolve_unknown_denomination_raises(client):
    with pytest.raises(LioGamesConfigError):
        client.resolve_variation_id(999)


# ── Mock-режим (без сети) ────────────────────────────────────────────────────

def test_mock_balance(client):
    data = client.balance()
    assert data["member_code"] == "M2609247OQFNWJJ8W"
    assert "balance" in data


def test_mock_order_create_is_idempotent_by_client_ref(client):
    a = client.order_create(534124, client_ref="shop-1001")
    b = client.order_create(534124, client_ref="shop-1001")
    assert a["order_id"] == b["order_id"]
    c = client.order_create(534124, client_ref="shop-1002")
    assert c["order_id"] != a["order_id"]


def test_mock_order_status_completed_yields_code(client):
    created = client.order_create(534124, client_ref="shop-1003")
    status = client.order_status(order_id=created["order_id"])
    assert LioGamesClient.status_is_terminal_ok(status)
    assert LioGamesClient.extract_code(status)


def test_order_create_requires_client_ref(client):
    with pytest.raises(LioGamesConfigError):
        client.order_create(534124, client_ref="")


def test_order_status_requires_identifier(client):
    with pytest.raises(LioGamesConfigError):
        client.order_status()


def test_order_create_without_product_id_raises():
    c = LioGamesClient(member_code="M1", secret="s", variations=VARIATIONS, mock=True)
    with pytest.raises(LioGamesConfigError):
        c.order_create(534124, client_ref="x")


# ── from_env ─────────────────────────────────────────────────────────────────

def test_from_env_builds_client():
    env = {
        "LIOG_MEMBER_CODE": "M2609247OQFNWJJ8W",
        "LIOG_SECRET": "s3cr3t",
        "LIOG_PRODUCT_ID": "66599",
        "LIOG_VARIATIONS": VARIATIONS,
        "LIOG_MOCK": "1",
    }
    c = LioGamesClient.from_env(env)
    assert c.member_code == "M2609247OQFNWJJ8W"
    assert c.product_id == 66599
    assert c.mock is True
    assert c.resolve_variation_id(660) == 534126
