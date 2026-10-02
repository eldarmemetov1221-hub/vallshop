"""Inline-клавиатуры."""

from __future__ import annotations

from decimal import Decimal
from typing import List, Mapping

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .db.models import Product, Variant
from .texts import OUT_OF_STOCK_NOTE, money


def products_kb(products: List[Product]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in products:
        kb.row(InlineKeyboardButton(text=p.title, callback_data=f"prod:{p.id}"))
    return kb.as_markup()


def variants_kb(
    variants: List[Variant],
    prices: Mapping[int, Decimal],
    stock: Mapping[int, int],
    currency: str = "USDT",
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for v in variants:
        price = money(prices[v.id], currency)
        in_stock = stock.get(v.id, 0)
        note = "" if in_stock > 0 else f" · {OUT_OF_STOCK_NOTE}"
        kb.row(
            InlineKeyboardButton(
                text=f"{v.title} — {price}{note}",
                callback_data=f"var:{v.id}",
            )
        )
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="catalog"))
    return kb.as_markup()


def buy_kb(variant_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💳 Купить", callback_data=f"buy:{variant_id}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="catalog"))
    return kb.as_markup()


# Человекочитаемые названия сетей USDT.
NETWORK_LABELS = {
    "TRC20": "TRON (TRC20)",
    "BEP20": "BNB Chain (BEP20)",
    "ERC20": "Ethereum (ERC20)",
    "POLYGON": "Polygon",
    "SOLANA": "Solana",
}


def network_label(net: str) -> str:
    return NETWORK_LABELS.get(net.upper(), net.upper())


def networks_kb(variant_id: int, networks: list[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for net in networks:
        kb.row(
            InlineKeyboardButton(
                text=f"USDT · {network_label(net)}",
                callback_data=f"net:{variant_id}:{net.upper()}",
            )
        )
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"var:{variant_id}"))
    return kb.as_markup()


def payment_kb(order_id: int, checkout_url: str | None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if checkout_url:
        kb.row(InlineKeyboardButton(text="🌐 Страница оплаты", url=checkout_url))
    kb.row(
        InlineKeyboardButton(
            text="🔄 Проверить оплату", callback_data=f"check:{order_id}"
        )
    )
    return kb.as_markup()
