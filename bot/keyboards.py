"""Клавиатуры бота.

Цвет кнопок (новое в Bot API) задаётся полем ``style``:
  • success → 🟢 зелёный, danger → 🔴 красный, primary → 🔵 синий.
Жёлтого/серого в API нет — для них оставляем обычный цвет + эмодзи.
Отключить цвета можно переменной окружения BUTTON_STYLES=0.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import List, Mapping, Optional

from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .db.models import Product, Variant
from .texts import OUT_OF_STOCK_NOTE, money

_STYLES = os.getenv("BUTTON_STYLES", "1").strip().lower() in {"1", "true", "yes", "on"}


def _b(text: str, *, style: Optional[str] = None, **kw) -> InlineKeyboardButton:
    """Кнопка с опциональным цветом (style игнорируется, если выключен)."""
    if style and _STYLES:
        return InlineKeyboardButton(text=text, style=style, **kw)
    return InlineKeyboardButton(text=text, **kw)


# ── Главное меню ──────────────────────────────────────────────────────────────
def main_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_b("🛍 Каталог", callback_data="catalog", style="primary"))
    kb.row(_b("🟢 Мой профиль", callback_data="profile", style="success"))
    return kb.as_markup()


# ── Каталог ───────────────────────────────────────────────────────────────────
def products_kb(products: List[Product]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in products:
        kb.row(_b(p.title, callback_data=f"prod:{p.id}", style="primary"))
    kb.row(_b("⬅️ Меню", callback_data="menu"))
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
        kb.row(_b(f"{v.title} — {price}{note}", callback_data=f"var:{v.id}"))
    kb.row(_b("⬅️ Назад", callback_data="catalog"))
    return kb.as_markup()


def buy_kb(variant_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_b("💳 Купить", callback_data=f"buy:{variant_id}", style="success"))
    kb.row(_b("⬅️ Назад", callback_data="catalog"))
    return kb.as_markup()


def after_purchase_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_b("🛍 В каталог", callback_data="catalog", style="primary"))
    kb.row(
        _b("🟢 Профиль", callback_data="profile", style="success"),
        _b("⬅️ Меню", callback_data="menu"),
    )
    return kb.as_markup()


def quantity_kb(
    variant_id: int, qty: int, total: Decimal, currency: str, max_qty: int
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    dec = f"qty:{variant_id}:{qty - 1}" if qty > 1 else "noop"
    inc = f"qty:{variant_id}:{qty + 1}" if qty < max_qty else "noop"
    kb.row(
        _b("➖", callback_data=dec),
        _b(f"{qty} шт", callback_data="noop"),
        _b("➕", callback_data=inc),
    )
    kb.row(
        _b(
            f"✅ Купить за {money(total, currency)}",
            callback_data=f"confirm:{variant_id}:{qty}",
            style="success",
        )
    )
    kb.row(_b("⬅️ Назад", callback_data=f"var:{variant_id}"))
    return kb.as_markup()


# ── Названия сетей ────────────────────────────────────────────────────────────
NETWORK_LABELS = {
    "TRC20": "TRON (TRC20)",
    "BEP20": "BNB Chain (BEP20)",
    "ERC20": "Ethereum (ERC20)",
    "POLYGON": "Polygon",
    "SOLANA": "Solana",
}
# Доступные в API цвета: TRC20 красный, остальные — обычные (жёлтого/серого нет).
NETWORK_STYLES = {"TRC20": "danger", "SOLANA": "success"}


def network_label(net: str) -> str:
    return NETWORK_LABELS.get(net.upper(), net.upper())


# ── Профиль ───────────────────────────────────────────────────────────────────
def profile_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_b("🔴 Мой баланс", callback_data="balance", style="danger"))
    kb.row(_b("🟡 Мои заказы", callback_data="myorders"))
    kb.row(_b("📜 История пополнений", callback_data="mytopups"))
    kb.row(_b("⬅️ Меню", callback_data="menu"))
    return kb.as_markup()


def balance_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_b("🟢 Пополнить баланс", callback_data="topup", style="success"))
    kb.row(_b("⬅️ Назад", callback_data="profile"))
    return kb.as_markup()


def topup_networks_kb(networks: list[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for net in networks:
        kb.row(
            _b(
                f"USDT · {network_label(net)}",
                callback_data=f"tunet:{net.upper()}",
                style=NETWORK_STYLES.get(net.upper()),
            )
        )
    kb.row(_b("⬅️ Отмена", callback_data="balance"))
    return kb.as_markup()


def topup_payment_kb(
    topup_id: int, checkout_url: str | None, address: str | None = None
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if address:
        kb.row(
            InlineKeyboardButton(
                text="📋 Скопировать адрес", copy_text=CopyTextButton(text=address)
            )
        )
    if checkout_url:
        kb.row(_b("🌐 Страница оплаты", url=checkout_url))
    kb.row(_b("🔄 Проверить оплату", callback_data=f"tucheck:{topup_id}", style="success"))
    kb.row(_b("⬅️ В профиль", callback_data="profile"))
    return kb.as_markup()


def back_profile_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_b("⬅️ В профиль", callback_data="profile"))
    return kb.as_markup()


def topup_cancel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_b("⬅️ Отмена", callback_data="balance"))
    return kb.as_markup()
