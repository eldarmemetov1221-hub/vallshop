"""Клавиатуры бота.

Примечание: цвет inline-кнопок в Telegram менять нельзя (все кнопки одного
цвета темы). «Цвета» из ТЗ переданы цветными кружками-эмодзи:
🟢 светло-зелёный, 🔴 светло-красный, 🟡 жёлтый, ⚪ светло-серый/обычный.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Mapping

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .db.models import Product, Variant
from .texts import OUT_OF_STOCK_NOTE, money


# ── Главное меню (inline) ─────────────────────────────────────────────────────
def main_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🛍 Каталог", callback_data="catalog"))
    kb.row(InlineKeyboardButton(text="🟢 Мой профиль", callback_data="profile"))
    return kb.as_markup()


# ── Каталог ───────────────────────────────────────────────────────────────────
def products_kb(products: List[Product]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in products:
        kb.row(InlineKeyboardButton(text=p.title, callback_data=f"prod:{p.id}"))
    kb.row(InlineKeyboardButton(text="⬅️ Меню", callback_data="menu"))
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


def after_purchase_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🛍 В каталог", callback_data="catalog"))
    kb.row(
        InlineKeyboardButton(text="🟢 Профиль", callback_data="profile"),
        InlineKeyboardButton(text="⬅️ Меню", callback_data="menu"),
    )
    return kb.as_markup()


def back_profile_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⬅️ В профиль", callback_data="profile"))
    return kb.as_markup()


def topup_cancel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⬅️ Отмена", callback_data="balance"))
    return kb.as_markup()


def quantity_kb(
    variant_id: int, qty: int, total: Decimal, currency: str, max_qty: int
) -> InlineKeyboardMarkup:
    """Степпер количества (−/N/+) + кнопка подтверждения покупки."""
    kb = InlineKeyboardBuilder()
    dec = f"qty:{variant_id}:{qty - 1}" if qty > 1 else "noop"
    inc = f"qty:{variant_id}:{qty + 1}" if qty < max_qty else "noop"
    kb.row(
        InlineKeyboardButton(text="➖", callback_data=dec),
        InlineKeyboardButton(text=f"{qty} шт", callback_data="noop"),
        InlineKeyboardButton(text="➕", callback_data=inc),
    )
    kb.row(
        InlineKeyboardButton(
            text=f"✅ Купить за {money(total, currency)}",
            callback_data=f"confirm:{variant_id}:{qty}",
        )
    )
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"var:{variant_id}"))
    return kb.as_markup()


# ── Названия сетей ────────────────────────────────────────────────────────────
NETWORK_LABELS = {
    "TRC20": "TRON (TRC20)",
    "BEP20": "BNB Chain (BEP20)",
    "ERC20": "Ethereum (ERC20)",
    "POLYGON": "Polygon",
    "SOLANA": "Solana",
}
# Цветовые кружки под сети (из ТЗ).
NETWORK_DOTS = {"TRC20": "🔴", "BEP20": "🟡", "ERC20": "⚪", "POLYGON": "🟣", "SOLANA": "🟢"}


def network_label(net: str) -> str:
    return NETWORK_LABELS.get(net.upper(), net.upper())


# ── Профиль ───────────────────────────────────────────────────────────────────
def profile_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔴 Мой баланс", callback_data="balance"))
    kb.row(InlineKeyboardButton(text="🟡 Мои заказы", callback_data="myorders"))
    kb.row(InlineKeyboardButton(text="📜 История пополнений", callback_data="mytopups"))
    kb.row(InlineKeyboardButton(text="⬅️ Меню", callback_data="menu"))
    return kb.as_markup()


def balance_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🟢 Пополнить баланс", callback_data="topup"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="profile"))
    return kb.as_markup()


def topup_networks_kb(networks: list[str]) -> InlineKeyboardMarkup:
    """Выбор сети для пополнения. Сумма уже сохранена в состоянии FSM."""
    kb = InlineKeyboardBuilder()
    for net in networks:
        dot = NETWORK_DOTS.get(net.upper(), "•")
        kb.row(
            InlineKeyboardButton(
                text=f"{dot} USDT · {network_label(net)}",
                callback_data=f"tunet:{net.upper()}",
            )
        )
    kb.row(InlineKeyboardButton(text="⬅️ Отмена", callback_data="balance"))
    return kb.as_markup()


def topup_payment_kb(topup_id: int, checkout_url: str | None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if checkout_url:
        kb.row(InlineKeyboardButton(text="🌐 Страница оплаты", url=checkout_url))
    kb.row(
        InlineKeyboardButton(
            text="🔄 Проверить оплату", callback_data=f"tucheck:{topup_id}"
        )
    )
    kb.row(InlineKeyboardButton(text="⬅️ В профиль", callback_data="profile"))
    return kb.as_markup()
