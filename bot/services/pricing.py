"""Расчёт цены продажи с учётом наценки.

Приоритет:
  1. ``variant.price_usd`` — фиксированная цена продажи (если задана).
  2. иначе ``variant.cost_usd`` * (1 + markup/100), где markup =
     ``variant.markup_percent`` (если задан), иначе дефолт из конфига.

Все деньги — Decimal, округление до 2 знаков (ROUND_HALF_UP).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from ..db.models import Variant

_CENT = Decimal("0.01")


def quantize_money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def effective_markup_percent(
    variant: Variant, default_markup_percent: Decimal
) -> Decimal:
    if variant.markup_percent is not None:
        return Decimal(variant.markup_percent)
    return Decimal(default_markup_percent)


def sale_price(variant: Variant, default_markup_percent: Decimal = Decimal("0")) -> Decimal:
    """Вернуть цену продажи для вариации."""
    if variant.price_usd is not None:
        return quantize_money(Decimal(variant.price_usd))

    cost = Decimal(variant.cost_usd or 0)
    markup = effective_markup_percent(variant, default_markup_percent)
    return quantize_money(cost * (Decimal(1) + markup / Decimal(100)))


def _fmt_rub(value: Decimal) -> str:
    d = Decimal(value)
    if d == d.to_integral_value():
        return f"{int(d)}"
    return f"{d:.2f}"


def price_label(variant: Variant, default_markup_percent: Decimal = Decimal("0")) -> str:
    """Короткая цена для кнопки/карточки: «₽ / $», только заданные валюты.

    USD (списание) берётся из sale_price; ₽ — из ручного price_rub (справочно).
    Если ни одна цена не задана — «—».
    """
    parts: list[str] = []
    rub = getattr(variant, "price_rub", None)
    if rub is not None:
        parts.append(f"{_fmt_rub(rub)} ₽")
    usd = sale_price(variant, default_markup_percent)
    if usd and usd > 0:
        parts.append(f"{usd:.2f} $")
    return " / ".join(parts) if parts else "—"


def margin(variant: Variant, default_markup_percent: Decimal = Decimal("0")) -> Decimal:
    """Абсолютная маржа (цена продажи минус закупка)."""
    return quantize_money(
        sale_price(variant, default_markup_percent) - Decimal(variant.cost_usd or 0)
    )
