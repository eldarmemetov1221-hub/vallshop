"""Двойная цена ₽ / $ (price_label)."""

from decimal import Decimal as D

from bot.db.models import Variant
from bot.services.pricing import price_label


def _v(**kw):
    kw.setdefault("title", "x")
    kw.setdefault("cost_usd", D("0"))
    return Variant(**kw)


def test_both_currencies():
    v = _v(price_usd=D("2.79"), price_rub=D("199"))
    assert price_label(v) == "199 ₽ / 2.79 $"


def test_only_usd():
    assert price_label(_v(price_usd=D("5.39"))) == "5.39 $"


def test_only_rub_when_usd_zero():
    assert price_label(_v(price_rub=D("150.50"))) == "150.50 ₽"


def test_none_set():
    assert price_label(_v()) == "—"


def test_rub_integer_has_no_decimals():
    assert price_label(_v(price_rub=D("200.00"), price_usd=D("3.00"))) == "200 ₽ / 3.00 $"
