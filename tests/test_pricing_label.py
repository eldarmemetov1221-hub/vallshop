"""Цена клиенту в рублях (price_label) — из USDT по курсу или ручная ₽."""

from decimal import Decimal as D

import pytest

from bot.db.models import Variant
from bot.services import rates as rates_service
from bot.services.pricing import price_label, price_rub_value


@pytest.fixture(autouse=True)
def _rate95():
    # Фиксируем курс для предсказуемости (по умолчанию 95).
    rates_service._rate = D("95")
    yield
    rates_service._rate = rates_service.DEFAULT_RATE


def _v(**kw):
    kw.setdefault("title", "x")
    kw.setdefault("cost_usd", D("0"))
    return Variant(**kw)


def test_manual_rub_priority():
    # Ручная цена ₽ имеет приоритет над расчётом по курсу.
    v = _v(price_usd=D("2.79"), price_rub=D("199"))
    assert price_label(v) == "199 ₽"


def test_computed_from_usd_rounds_up():
    # 5.39 USDT × 95 = 512.05 → вверх до 513 ₽
    assert price_label(_v(price_usd=D("5.39"))) == "513 ₽"


def test_computed_whole_ruble():
    # 2.00 USDT × 95 = 190 ₽ ровно
    assert price_label(_v(price_usd=D("2.00"))) == "190 ₽"


def test_none_set():
    assert price_label(_v()) == "—"


def test_price_rub_value_manual():
    assert price_rub_value(_v(price_rub=D("150"))) == D("150")
