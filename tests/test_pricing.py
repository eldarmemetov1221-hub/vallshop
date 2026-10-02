"""Тесты расчёта цены с наценкой."""

from decimal import Decimal

from bot.db.models import Variant
from bot.services.pricing import margin, sale_price


def _variant(cost="0.88", price=None, markup=None) -> Variant:
    return Variant(
        product_id=1,
        title="60 UC",
        liog_product_id=66599,
        liog_variation_id=534124,
        cost_usd=Decimal(cost),
        price_usd=Decimal(price) if price is not None else None,
        markup_percent=Decimal(markup) if markup is not None else None,
    )


def test_default_markup_zero_equals_cost():
    v = _variant(cost="0.88")
    assert sale_price(v, Decimal("0")) == Decimal("0.88")


def test_global_default_markup_applied():
    v = _variant(cost="10.00")
    assert sale_price(v, Decimal("20")) == Decimal("12.00")


def test_variant_markup_overrides_default():
    v = _variant(cost="10.00", markup="50")
    assert sale_price(v, Decimal("20")) == Decimal("15.00")


def test_fixed_price_overrides_markup():
    v = _variant(cost="10.00", price="13.37", markup="50")
    assert sale_price(v, Decimal("20")) == Decimal("13.37")


def test_rounding_half_up():
    # 4.44 * 1.15 = 5.106 -> 5.11
    v = _variant(cost="4.44", markup="15")
    assert sale_price(v, Decimal("0")) == Decimal("5.11")


def test_margin():
    v = _variant(cost="10.00", markup="30")
    assert sale_price(v, Decimal("0")) == Decimal("13.00")
    assert margin(v, Decimal("0")) == Decimal("3.00")
