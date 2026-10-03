"""Клавиатуры бота.

Цвет кнопок (Bot API ``style``): success → 🟢, danger → 🔴, primary → 🔵.
Кастом-эмодзи (премиум) на кнопках — ``icon_custom_emoji_id`` (нужен Premium у
владельца бота). Переключатели: BUTTON_STYLES=0 и CUSTOM_EMOJI=0.
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


def _truthy(v: str) -> bool:
    return v.strip().lower() in {"1", "true", "yes", "on"}


_STYLES = _truthy(os.getenv("BUTTON_STYLES", "1"))
_CUSTOM_EMOJI = _truthy(os.getenv("CUSTOM_EMOJI", "1"))

# Кастом-эмодзи (custom_emoji_id) для кнопок.
EMOJI = {
    "catalog": "5805550320985578625",
    "profile": "6035084557378654059",
    "balance": "5778421276024509124",
    "orders": "5904359114531675993",
    "topups": "6039859895291877126",
    "topup": "5890848474563352982",
    "back": "6039539366177541657",  # назад/отмена/меню
    "product": "5298953332079999355",   # товар PUBG Mobile
    "variant": "5242743089327513390",   # номиналы UC
    "admin": "5778570255555105942",     # админ-панель
    "copy": "6028171274939797252",       # скопировать адрес
    "paylink": "5902206159095339799",    # страница оплаты
    "check": "6039859895291877126",      # проверить оплату
    "faq": "6030848053177486888",        # FAQ / Правила
    "offer": "6030445631921721471",      # публичная оферта
    "agreement": "6030445631921721471",  # пользовательское соглашение
    "privacy": "5935757052042285202",    # политика конфиденциальности
}


def _b(
    text: str, *, style: Optional[str] = None, icon: Optional[str] = None, **kw
) -> InlineKeyboardButton:
    """Кнопка с опциональным цветом (style) и кастом-эмодзи (icon)."""
    if style and _STYLES:
        kw["style"] = style
    if icon and _CUSTOM_EMOJI:
        kw["icon_custom_emoji_id"] = icon
    return InlineKeyboardButton(text=text, **kw)


# ── Главное меню ──────────────────────────────────────────────────────────────
def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        _b("Каталог", callback_data="catalog", style="primary", icon=EMOJI["catalog"]),
        _b("Мой профиль", callback_data="profile", style="success", icon=EMOJI["profile"]),
    )
    kb.row(_b("FAQ / Правила", callback_data="faq", style="danger", icon=EMOJI["faq"]))
    if is_admin:
        kb.row(_b("Админ-панель", callback_data="admin", style="danger", icon=EMOJI["admin"]))
    return kb.as_markup()


def faq_kb(
    offer_url: Optional[str] = None,
    agreement_url: Optional[str] = None,
    privacy_url: Optional[str] = None,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    # Политика конфиденциальности + Политика соглашения — в один ряд.
    if privacy_url and agreement_url:
        kb.row(
            _b("Политика конфиденциальности", url=privacy_url, icon=EMOJI["privacy"]),
            _b("Политика соглашения", url=agreement_url, icon=EMOJI["agreement"]),
        )
    elif privacy_url:
        kb.row(_b("Политика конфиденциальности", url=privacy_url, icon=EMOJI["privacy"]))
    elif agreement_url:
        kb.row(_b("Политика соглашения", url=agreement_url, icon=EMOJI["agreement"]))
    # Публичная оферта — снизу.
    if offer_url:
        kb.row(_b("Публичная оферта", url=offer_url, style="success", icon=EMOJI["offer"]))
    kb.row(_b("Меню", callback_data="menu", icon=EMOJI["back"]))
    return kb.as_markup()


# ── Каталог ───────────────────────────────────────────────────────────────────
def products_kb(products: List[Product]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in products:
        icon = getattr(p, "icon_emoji_id", None) or EMOJI["product"]
        kb.row(_b(p.title, callback_data=f"prod:{p.id}", style="primary", icon=icon))
    kb.row(_b("Меню", callback_data="menu", icon=EMOJI["back"]))
    return kb.as_markup()


def variants_kb(
    variants: List[Variant],
    prices: Mapping[int, Decimal],
    stock: Mapping[int, int],
    currency: str = "USDT",
    product_icon: Optional[str] = None,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for v in variants:
        price = money(prices[v.id], currency)
        if getattr(v, "source", "stock") == "fazercard":
            st = stock.get(v.id)
            if st is None:
                note = " · ✅ в наличии"
            elif st > 0:
                note = f" · ✅ {st} шт"
            else:
                note = " · ❌ нет"
        else:
            in_stock = stock.get(v.id, 0) or 0
            note = "" if in_stock > 0 else f" · {OUT_OF_STOCK_NOTE}"
        icon = getattr(v, "icon_emoji_id", None) or product_icon or EMOJI["variant"]
        kb.row(_b(f"{v.title} — {price}{note}", callback_data=f"var:{v.id}", icon=icon))
    kb.row(_b("Назад", callback_data="catalog", icon=EMOJI["back"]))
    return kb.as_markup()


def buy_kb(variant_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_b("💳 Купить", callback_data=f"buy:{variant_id}", style="success"))
    kb.row(_b("Назад", callback_data="catalog", icon=EMOJI["back"]))
    return kb.as_markup()


def after_purchase_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_b("Каталог", callback_data="catalog", style="primary", icon=EMOJI["catalog"]))
    kb.row(
        _b("Профиль", callback_data="profile", style="success", icon=EMOJI["profile"]),
        _b("Меню", callback_data="menu", icon=EMOJI["back"]),
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
    kb.row(_b("Назад", callback_data=f"var:{variant_id}", icon=EMOJI["back"]))
    return kb.as_markup()


# ── Названия сетей ────────────────────────────────────────────────────────────
NETWORK_LABELS = {
    "TRC20": "TRON (TRC20)",
    "BEP20": "BNB Chain (BEP20)",
    "ERC20": "Ethereum (ERC20)",
    "POLYGON": "Polygon",
    "SOLANA": "Solana",
}
NETWORK_STYLES = {"TRC20": "danger", "SOLANA": "success"}


def network_label(net: str) -> str:
    return NETWORK_LABELS.get(net.upper(), net.upper())


# ── Профиль ───────────────────────────────────────────────────────────────────
def profile_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_b("Мой баланс", callback_data="balance", style="danger", icon=EMOJI["balance"]))
    kb.row(_b("Мои заказы", callback_data="myorders", style="primary", icon=EMOJI["orders"]))
    kb.row(_b("История пополнений", callback_data="mytopups", icon=EMOJI["topups"]))
    kb.row(_b("Меню", callback_data="menu", icon=EMOJI["back"]))
    return kb.as_markup()


def balance_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_b("Пополнить баланс", callback_data="topup", style="success", icon=EMOJI["topup"]))
    kb.row(_b("Назад", callback_data="profile", icon=EMOJI["back"]))
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
    kb.row(_b("Отмена", callback_data="balance", icon=EMOJI["back"]))
    return kb.as_markup()


def topup_payment_kb(
    topup_id: int, checkout_url: str | None, address: str | None = None
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if address:
        kb.row(
            _b(
                "Скопировать адрес",
                icon=EMOJI["copy"],
                copy_text=CopyTextButton(text=address),
            )
        )
    if checkout_url:
        kb.row(_b("Страница оплаты", url=checkout_url, icon=EMOJI["paylink"]))
    kb.row(
        _b("Проверить оплату", callback_data=f"tucheck:{topup_id}", style="success", icon=EMOJI["check"])
    )
    kb.row(_b("В профиль", callback_data="profile", icon=EMOJI["back"]))
    return kb.as_markup()


def back_profile_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_b("В профиль", callback_data="profile", icon=EMOJI["back"]))
    return kb.as_markup()


def topup_cancel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_b("Отмена", callback_data="balance", icon=EMOJI["back"]))
    return kb.as_markup()
