"""Инлайн админ-панель (видна только ADMIN_IDS).

Управление каталогом кнопками: товары, номиналы, цена/наценка, сток, видимость,
заказы. Ввод значений — короткими сообщениями (FSM). Текстовые команды из
admin.py тоже продолжают работать как запасной вариант.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
from typing import Optional

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from ..config import BotConfig
from ..db import Database
from ..db.models import Order, Product, StockItem, StockStatus, User, Variant
from ..services import catalog as catalog_service
from ..services import stock as stock_service
from ..services.pricing import _fmt_rub, margin, price_label, sale_price
from fazercard import FazerCardClient, FazerCardError
from .. import texts

router = Router()


class IsAdminCb(BaseFilter):
    async def __call__(self, call: CallbackQuery, config: BotConfig) -> bool:
        return bool(call.from_user and config.is_admin(call.from_user.id))


class IsAdminMsg(BaseFilter):
    async def __call__(self, message: Message, config: BotConfig) -> bool:
        return bool(message.from_user and config.is_admin(message.from_user.id))


router.callback_query.filter(IsAdminCb())
router.message.filter(IsAdminMsg())


class AdminUI(StatesGroup):
    add_product = State()
    add_variant = State()
    set_price = State()
    set_markup = State()
    add_stock = State()
    set_prod_emoji = State()
    set_var_emoji = State()
    set_prod_title = State()
    set_prod_desc = State()
    add_variant_manual = State()
    add_subcat = State()
    set_var_title = State()
    set_price_rub = State()
    set_text = State()
    fzr_search = State()
    fzr_price = State()


def _first_custom_emoji(message: Message) -> Optional[str]:
    """Вернуть custom_emoji_id первого премиум-эмодзи в сообщении, если есть."""
    entities = (message.entities or []) + (message.caption_entities or [])
    for e in entities:
        cid = getattr(e, "custom_emoji_id", None)
        if cid:
            return cid
    return None


def _emoji_id_from(message: Message) -> Optional[str]:
    """custom_emoji_id из присланного премиум-эмодзи ИЛИ из набранного числа-ID."""
    cid = _first_custom_emoji(message)
    if cid:
        return cid
    raw = (message.text or "").strip()
    return raw if raw.isdigit() and 5 <= len(raw) <= 25 else None


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


# ── Рендер экранов ───────────────────────────────────────────────────────────
async def _panel_caption(session) -> str:
    products = await session.scalar(select(func.count()).select_from(Product))
    variants = await session.scalar(select(func.count()).select_from(Variant))
    in_stock = await session.scalar(
        select(func.count()).select_from(StockItem).where(
            StockItem.status == StockStatus.AVAILABLE
        )
    )
    orders = await session.scalar(select(func.count()).select_from(Order))
    return (
        "🛠 <b>Админ-панель VallShop</b>\n\n"
        f"Товаров: <b>{products}</b>\n"
        f"Номиналов: <b>{variants}</b>\n"
        f"Кодов в стоке: <b>{in_stock}</b>\n"
        f"Заказов: <b>{orders}</b>"
    )


def _panel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("📦 Товары", "a_prods"))
    kb.row(_btn("➕ Добавить товар", "a_addprod"))
    kb.row(_btn("🧾 Заказы", "a_orders"), _btn("📥 Сток", "a_stock"))
    kb.row(_btn("🎟 Коды активации", "ac_home"))
    kb.row(_btn("📊 Статистика", "st_home"))
    kb.row(_btn("⬅️ Меню", "menu"))
    return kb.as_markup()


async def _show_panel(call: CallbackQuery, db: Database, edit: bool) -> None:
    async with db.session() as session:
        caption = await _panel_caption(session)
    if edit:
        try:
            await call.message.edit_text(caption, reply_markup=_panel_kb())
            return
        except Exception:  # noqa: BLE001
            pass
    await call.message.answer(caption, reply_markup=_panel_kb())


@router.callback_query(F.data == "admin")
async def cb_admin(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    # Открываем панель новым сообщением (меню — это фото, его не edit_text'нуть).
    await _show_panel(call, db, edit=False)
    await call.answer()


@router.callback_query(F.data == "a_home")
async def cb_admin_home(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    await _show_panel(call, db, edit=True)
    await call.answer()


# ── Товары ───────────────────────────────────────────────────────────────────
async def _products_kb(session) -> InlineKeyboardMarkup:
    products = await catalog_service.list_products(session, only_active=False)
    kb = InlineKeyboardBuilder()
    for p in products:
        flag = "🟢" if p.is_active else "🔴"
        kb.row(_btn(f"{flag} {p.title}", f"a_prod:{p.id}"))
    kb.row(_btn("➕ Добавить товар", "a_addprod"))
    kb.row(_btn("⬅️ Назад", "a_home"))
    return kb.as_markup()


@router.callback_query(F.data == "a_prods")
async def cb_products(call: CallbackQuery, db: Database) -> None:
    async with db.session() as session:
        markup = await _products_kb(session)
    await call.message.edit_text("📦 <b>Товары</b>\nВыберите товар:", reply_markup=markup)
    await call.answer()


async def _product_card(session, config: BotConfig, product_id: int):
    product = await session.get(Product, product_id)
    if not product:
        return None, None
    children = await catalog_service.list_children(session, product_id, only_active=False)
    variants = await catalog_service.list_variants(session, product_id, only_active=False)
    counts = await stock_service.counts_by_variant(session, [v.id for v in variants])
    flag = "🟢 активен" if product.is_active else "🔴 выключен"
    desc = product.description or "— (нет описания)"
    kind = "📁 категория" if children else "📦 товар"
    caption = (
        f"{kind}: <b>{product.title}</b>\n"
        f"Игра: {product.game} · {flag}\n"
        f"Описание: {desc}\n"
        f"Подкатегорий: {len(children)} · Номиналов: {len(variants)}"
    )
    kb = InlineKeyboardBuilder()
    # Подкатегории (навигация вглубь).
    for c in children:
        cflag = "🟢" if c.is_active else "🔴"
        kb.row(_btn(f"{cflag} 📁 {c.title}", f"a_prod:{c.id}"))
    for v in variants:
        vflag = "🟢" if v.is_active else "🔴"
        kb.row(
            _btn(
                f"{vflag} {v.title} · {price_label(v, config.default_markup_percent)} · сток {counts.get(v.id, 0)}",
                f"a_var:{v.id}",
            )
        )
    kb.row(
        _btn("✏️ Название", f"a_ptitle:{product_id}"),
        _btn("📝 Описание", f"a_pdesc:{product_id}"),
    )
    kb.row(_btn("➕ Подкатегория", f"a_addsub:{product_id}"))
    kb.row(
        _btn("➕ Номинал (LioGames)", f"a_addvar:{product_id}"),
        _btn("➕ Свой номинал", f"a_addvarm:{product_id}"),
    )
    kb.row(_btn("➕ Номинал FazerCard", f"a_addvarf:{product_id}"))
    kb.row(
        _btn("🙂 Эмодзи", f"a_pemoji:{product_id}"),
        _btn("🔁 Вкл/выкл товар", f"a_tprod:{product_id}"),
    )
    kb.row(
        _btn("⏳ Текст ожидания", f"a_txt:p:pend:{product_id}"),
        _btn("✅ Текст выдачи", f"a_txt:p:deliv:{product_id}"),
    )
    back = f"a_prod:{product.parent_id}" if product.parent_id else "a_prods"
    kb.row(_btn("⬅️ Назад", back))
    return caption, kb.as_markup()


@router.callback_query(F.data.startswith("a_prod:"))
async def cb_product(
    call: CallbackQuery, db: Database, config: BotConfig, state: FSMContext
) -> None:
    await state.clear()  # сбросить любой незавершённый ввод (в т.ч. отмену браузера)
    pid = int(call.data.split(":", 1)[1])
    async with db.session() as session:
        caption, markup = await _product_card(session, config, pid)
    if not caption:
        await call.answer("Не найдено", show_alert=True)
        return
    await call.message.edit_text(caption, reply_markup=markup)
    await call.answer()


@router.callback_query(F.data.startswith("a_tprod:"))
async def cb_toggle_product(call: CallbackQuery, db: Database, config: BotConfig) -> None:
    pid = int(call.data.split(":", 1)[1])
    async with db.session() as session:
        product = await session.get(Product, pid)
        if product:
            product.is_active = not product.is_active
            await session.commit()
        caption, markup = await _product_card(session, config, pid)
    await call.message.edit_text(caption, reply_markup=markup)
    await call.answer("Готово")


# ── Номинал (вариация) ───────────────────────────────────────────────────────
async def _variant_card(session, config: BotConfig, variant_id: int):
    v = await session.get(Variant, variant_id)
    if not v:
        return None, None, None
    in_stock = await stock_service.available_count(session, variant_id)
    price = sale_price(v, config.default_markup_percent)
    m = margin(v, config.default_markup_percent)
    vflag = "🟢 активен" if v.is_active else "🔴 выключен"
    price_src = (
        f"фикс {texts.money(Decimal(v.price_usd), config.currency)}"
        if v.price_usd is not None
        else (f"наценка {v.markup_percent}%" if v.markup_percent is not None
              else f"наценка по умолчанию {config.default_markup_percent}%")
    )
    rub_line = (
        f"Цена ₽: <b>{_fmt_rub(Decimal(v.price_rub))} ₽</b>\n"
        if v.price_rub is not None else "Цена ₽: — (не задана)\n"
    )
    caption = (
        f"🧩 <b>{v.title}</b> ({vflag})\n"
        f"Закуп: {texts.money(Decimal(v.cost_usd or 0), config.currency)}\n"
        f"Цена продажи: <b>{texts.money(price, config.currency)}</b> ({price_src})\n"
        + rub_line +
        f"Маржа: {texts.money(m, config.currency)}\n"
        f"Сток: <b>{in_stock}</b>\n"
        + (
            f"Тип: FazerCard · {v.fzr_kind} "
            f"(a={v.fzr_a}, b={v.fzr_b}) · под заказ"
            if v.source == "fazercard"
            else "Тип: свой товар (без LioGames)"
            if not v.liog_product_id
            else f"LioGames: product {v.liog_product_id} / variation {v.liog_variation_id}"
        )
    )
    kb = InlineKeyboardBuilder()
    kb.row(_btn("✏️ Название номинала", f"a_vtitle:{variant_id}"))
    kb.row(
        _btn("💲 Цена $", f"a_setprice:{variant_id}"),
        _btn("💱 Цена ₽", f"a_setrub:{variant_id}"),
    )
    kb.row(
        _btn("📈 Наценка", f"a_setmarkup:{variant_id}"),
        _btn("📥 Добавить сток", f"a_astock:{variant_id}"),
    )
    kb.row(
        _btn("🙂 Эмодзи", f"a_vemoji:{variant_id}"),
        _btn("🔁 Вкл/выкл", f"a_tvar:{variant_id}"),
    )
    kb.row(
        _btn("⏳ Текст ожидания", f"a_txt:v:pend:{variant_id}"),
        _btn("✅ Текст выдачи", f"a_txt:v:deliv:{variant_id}"),
    )
    kb.row(_btn("⬅️ Назад", f"a_prod:{v.product_id}"))
    return caption, kb.as_markup(), v.product_id


@router.callback_query(F.data.startswith("a_var:"))
async def cb_variant(call: CallbackQuery, db: Database, config: BotConfig) -> None:
    vid = int(call.data.split(":", 1)[1])
    async with db.session() as session:
        caption, markup, _ = await _variant_card(session, config, vid)
    if not caption:
        await call.answer("Не найдено", show_alert=True)
        return
    await call.message.edit_text(caption, reply_markup=markup)
    await call.answer()


@router.callback_query(F.data.startswith("a_tvar:"))
async def cb_toggle_variant(call: CallbackQuery, db: Database, config: BotConfig) -> None:
    vid = int(call.data.split(":", 1)[1])
    async with db.session() as session:
        v = await session.get(Variant, vid)
        if v:
            v.is_active = not v.is_active
            await session.commit()
        caption, markup, _ = await _variant_card(session, config, vid)
    await call.message.edit_text(caption, reply_markup=markup)
    await call.answer("Готово")


# ── FSM: ввод значений ───────────────────────────────────────────────────────
def _cancel_kb(back: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ Отмена", back))
    return kb.as_markup()


@router.callback_query(F.data == "a_addprod")
async def cb_add_product(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminUI.add_product)
    await call.message.edit_text(
        "➕ <b>Новый товар</b>\n\nПришлите: <code>игра | Название | описание</code>\n"
        "Пример: <code>PUBG | PUBG Mobile UC (Global) | коды UC</code>",
        reply_markup=_cancel_kb("a_home"),
    )
    await call.answer()


@router.message(AdminUI.add_product)
async def msg_add_product(message: Message, db: Database, state: FSMContext) -> None:
    parts = [p.strip() for p in (message.text or "").split("|")]
    if len(parts) < 2:
        await message.answer("Формат: игра | Название [| описание]")
        return
    game, title = parts[0], parts[1]
    desc = parts[2] if len(parts) > 2 else None
    async with db.session() as session:
        p = Product(game=game, title=title, description=desc)
        session.add(p)
        await session.commit()
        pid = p.id
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(_btn("➕ Добавить номинал", f"a_addvar:{pid}"))
    kb.row(_btn("⬅️ К товару", f"a_prod:{pid}"))
    await message.answer(f"✅ Товар создан: <b>{title}</b>", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("a_addvar:"))
async def cb_add_variant(call: CallbackQuery, state: FSMContext) -> None:
    pid = int(call.data.split(":", 1)[1])
    await state.set_state(AdminUI.add_variant)
    await state.update_data(pid=pid)
    await call.message.edit_text(
        "➕ <b>Новый номинал</b>\n\nПришлите: "
        "<code>Название | liog_product_id | liog_variation_id | закуп_usd [| цена_usd]</code>\n"
        "Пример: <code>325 UC | 66599 | 534125 | 4.44</code>",
        reply_markup=_cancel_kb(f"a_prod:{pid}"),
    )
    await call.answer()


@router.message(AdminUI.add_variant)
async def msg_add_variant(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    pid = data.get("pid")
    parts = [p.strip() for p in (message.text or "").split("|")]
    if len(parts) < 4:
        await message.answer("Формат: Название | liog_product_id | liog_variation_id | закуп_usd [| цена_usd]")
        return
    try:
        liog_pid = int(parts[1]); liog_vid = int(parts[2]); cost = Decimal(parts[3])
    except (ValueError, InvalidOperation):
        await message.answer("liog_product_id/liog_variation_id — числа, закуп — число (4.44)")
        return
    price = None
    if len(parts) > 4 and parts[4] not in ("", "-"):
        try:
            price = Decimal(parts[4])
        except InvalidOperation:
            await message.answer("цена_usd — число или - ")
            return
    async with db.session() as session:
        v = Variant(
            product_id=pid, title=parts[0],
            liog_product_id=liog_pid, liog_variation_id=liog_vid,
            cost_usd=cost, price_usd=price,
        )
        session.add(v)
        await session.commit()
        vid = v.id
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(_btn("📥 Добавить сток", f"a_astock:{vid}"))
    kb.row(_btn("⬅️ К товару", f"a_prod:{pid}"))
    await message.answer(f"✅ Номинал создан: <b>{parts[0]}</b>", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("a_addsub:"))
async def cb_add_subcat(call: CallbackQuery, state: FSMContext) -> None:
    pid = int(call.data.split(":", 1)[1])
    await state.set_state(AdminUI.add_subcat)
    await state.update_data(parent_id=pid)
    await call.message.edit_text(
        "➕ <b>Новая подкатегория</b>\n\nПришлите её название "
        "(например <code>PUBG UC Global</code> или <code>PUBG UC RU</code>).\n"
        "Можно с описанием: <code>Название | описание</code>.",
        reply_markup=_cancel_kb(f"a_prod:{pid}"),
    )
    await call.answer()


@router.message(AdminUI.add_subcat)
async def msg_add_subcat(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    parent_id = data.get("parent_id")
    parts = [p.strip() for p in (message.text or "").split("|")]
    title = parts[0] if parts else ""
    if not title:
        await message.answer("Название пустое. Пришлите текст.")
        return
    desc = parts[1] if len(parts) > 1 and parts[1] else None
    async with db.session() as session:
        parent = await session.get(Product, parent_id)
        if not parent:
            await state.clear()
            await message.answer("Родительский товар не найден.")
            return
        child = Product(
            game=parent.game, title=title, description=desc,
            parent_id=parent_id,
        )
        session.add(child)
        await session.commit()
        cid = child.id
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(_btn("➕ Номинал (LioGames)", f"a_addvar:{cid}"))
    kb.row(_btn("➕ Номинал FazerCard", f"a_addvarf:{cid}"))
    kb.row(_btn("⬅️ К подкатегории", f"a_prod:{cid}"))
    kb.row(_btn("⬅️ К родителю", f"a_prod:{parent_id}"))
    await message.answer(
        f"✅ Подкатегория создана: <b>{title}</b>\nДобавьте в неё номиналы.",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("a_ptitle:"))
async def cb_prod_title(call: CallbackQuery, state: FSMContext) -> None:
    pid = int(call.data.split(":", 1)[1])
    await state.set_state(AdminUI.set_prod_title)
    await state.update_data(pid=pid)
    await call.message.edit_text(
        "✏️ Пришлите новое <b>название</b> товара.",
        reply_markup=_cancel_kb(f"a_prod:{pid}"),
    )
    await call.answer()


@router.message(AdminUI.set_prod_title)
async def msg_prod_title(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    pid = data.get("pid")
    title = (message.text or "").strip()
    if not title:
        await message.answer("Название пустое. Пришлите текст.")
        return
    async with db.session() as session:
        p = await session.get(Product, pid)
        if p:
            p.title = title
            await session.commit()
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ К товару", f"a_prod:{pid}"))
    await message.answer("✅ Название обновлено", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("a_pdesc:"))
async def cb_prod_desc(call: CallbackQuery, state: FSMContext) -> None:
    pid = int(call.data.split(":", 1)[1])
    await state.set_state(AdminUI.set_prod_desc)
    await state.update_data(pid=pid)
    await call.message.edit_text(
        "📝 Пришлите <b>описание</b> товара (покажется покупателю при клике) "
        "или <code>-</code>, чтобы убрать.",
        reply_markup=_cancel_kb(f"a_prod:{pid}"),
    )
    await call.answer()


@router.message(AdminUI.set_prod_desc)
async def msg_prod_desc(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    pid = data.get("pid")
    raw = (message.text or "").strip()
    async with db.session() as session:
        p = await session.get(Product, pid)
        if p:
            p.description = None if raw == "-" else raw
            await session.commit()
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ К товару", f"a_prod:{pid}"))
    await message.answer("✅ Описание обновлено", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("a_addvarm:"))
async def cb_add_variant_manual(call: CallbackQuery, state: FSMContext) -> None:
    pid = int(call.data.split(":", 1)[1])
    await state.set_state(AdminUI.add_variant_manual)
    await state.update_data(pid=pid)
    await call.message.edit_text(
        "➕ <b>Свой номинал</b> (без LioGames, выдача из стока)\n\n"
        "Пришлите: <code>Название | закуп_usd [| цена_usd]</code>\n"
        "Пример: <code>Аккаунт Netflix | 2.00 | 5.00</code>",
        reply_markup=_cancel_kb(f"a_prod:{pid}"),
    )
    await call.answer()


@router.message(AdminUI.add_variant_manual)
async def msg_add_variant_manual(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    pid = data.get("pid")
    parts = [p.strip() for p in (message.text or "").split("|")]
    if len(parts) < 2:
        await message.answer("Формат: Название | закуп_usd [| цена_usd]")
        return
    try:
        cost = Decimal(parts[1])
    except InvalidOperation:
        await message.answer("закуп_usd — число (например 2.00)")
        return
    price = None
    if len(parts) > 2 and parts[2] not in ("", "-"):
        try:
            price = Decimal(parts[2])
        except InvalidOperation:
            await message.answer("цена_usd — число или -")
            return
    async with db.session() as session:
        # Уникальный отрицательный liog_variation_id (своё, не из LioGames).
        min_vid = await session.scalar(
            select(func.min(Variant.liog_variation_id)).where(Variant.product_id == pid)
        )
        new_vid = min(0, int(min_vid or 0)) - 1
        v = Variant(
            product_id=pid, title=parts[0],
            liog_product_id=0, liog_variation_id=new_vid,
            cost_usd=cost, price_usd=price,
        )
        session.add(v)
        await session.commit()
        vid = v.id
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(_btn("📥 Добавить сток", f"a_astock:{vid}"))
    kb.row(_btn("⬅️ К товару", f"a_prod:{pid}"))
    await message.answer(
        f"✅ Свой номинал создан: <b>{parts[0]}</b>\nДобавьте коды в сток.",
        reply_markup=kb.as_markup(),
    )


# ── Номинал FazerCard (buy-on-demand): браузер каталога ────────────────────────
_FZR_KINDS = {
    "gamekey": "ключ игры",
    "giftcard": "подарочная карта",
    "topup": "пополнение игры",
}
_FZR_RESULTS = 20   # сколько категорий показывать по поиску
_FZR_OFFERS = 40    # сколько номиналов показывать в категории


@router.callback_query(F.data.startswith("a_addvarf:"))
async def cb_add_variant_fzr(call: CallbackQuery, state: FSMContext) -> None:
    pid = int(call.data.split(":", 1)[1])
    await state.update_data(pid=pid)
    kb = InlineKeyboardBuilder()
    kb.row(_btn("🔑 Ключ игры", "a_fzrk:gamekey"))
    kb.row(_btn("🎁 Подарочная карта", "a_fzrk:giftcard"))
    kb.row(_btn("💠 Пополнение игры", "a_fzrk:topup"))
    kb.row(_btn("⬅️ Назад", f"a_prod:{pid}"))
    await call.message.edit_text(
        "➕ <b>Номинал FazerCard</b> (под заказ)\n\nВыберите тип:",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("a_fzrk:"))
async def cb_fzr_kind(call: CallbackQuery, state: FSMContext) -> None:
    kind = call.data.split(":", 1)[1]
    if kind not in _FZR_KINDS:
        await call.answer("Неизвестный тип", show_alert=True)
        return
    data = await state.get_data()
    pid = data.get("pid")
    await state.update_data(kind=kind)
    await state.set_state(AdminUI.fzr_search)
    await call.message.edit_text(
        f"🔎 <b>FazerCard · {_FZR_KINDS[kind]}</b>\n\n"
        "Пришлите название или его часть — например <code>amazon</code>, "
        "<code>steam</code>, <code>pubg</code>.",
        reply_markup=_cancel_kb(f"a_prod:{pid}"),
    )
    await call.answer()


@router.message(AdminUI.fzr_search)
async def msg_fzr_search(
    message: Message, db: Database, state: FSMContext, fzr: FazerCardClient
) -> None:
    data = await state.get_data()
    pid, kind = data.get("pid"), data.get("kind")
    query = (message.text or "").strip().lower()
    if not query:
        await message.answer("Пришлите слово для поиска.")
        return
    try:
        cats = await asyncio.to_thread(fzr.all_categories, kind)
    except FazerCardError as e:
        await message.answer(f"Ошибка каталога FazerCard: {e}")
        return
    matched = [
        c for c in cats
        if query in str(c.get("name") or "").lower()
        or query in str(c.get("id") or "").lower()
    ]
    if not matched:
        await message.answer("Ничего не найдено. Попробуйте другое слово.")
        return
    matched = matched[:_FZR_RESULTS]
    await state.update_data(cats=matched)
    kb = InlineKeyboardBuilder()
    for i, c in enumerate(matched):
        kb.row(_btn(c.get("name") or str(c.get("id")), f"a_fzrc:{i}"))
    kb.row(_btn("⬅️ Назад", f"a_prod:{pid}"))
    more = "" if len(matched) < _FZR_RESULTS else "\n(первые 20 — уточните запрос)"
    await message.answer(
        f"Выберите категорию:{more}", reply_markup=kb.as_markup()
    )


@router.callback_query(AdminUI.fzr_search, F.data.startswith("a_fzrc:"))
async def cb_fzr_cat(call: CallbackQuery, state: FSMContext, fzr: FazerCardClient) -> None:
    data = await state.get_data()
    cats = data.get("cats") or []
    i = int(call.data.split(":", 1)[1])
    if i >= len(cats):
        await call.answer("Список устарел, начните заново", show_alert=True)
        return
    cat, kind, pid = cats[i], data.get("kind"), data.get("pid")
    try:
        offers = await asyncio.to_thread(fzr.offers_for, kind, cat["id"])
    except FazerCardError as e:
        await call.answer(f"Ошибка: {e}", show_alert=True)
        return
    offers = [o for o in offers if o.get("id")][:_FZR_OFFERS]
    if not offers:
        await call.answer("Нет доступных номиналов", show_alert=True)
        return
    await state.update_data(cat=cat, offers=offers)
    kb = InlineKeyboardBuilder()
    for j, o in enumerate(offers):
        label = o.get("name") or str(o.get("id"))
        extra = []
        if o.get("price_usd"):
            extra.append(f"${o['price_usd']}")
        if o.get("stock") is not None:
            extra.append(f"сток {o['stock']}")
        if extra:
            label += " · " + " · ".join(extra)
        kb.row(_btn(label, f"a_fzro:{j}"))
    kb.row(_btn("⬅️ Назад", f"a_prod:{pid}"))
    await call.message.edit_text(
        f"🎯 <b>{cat.get('name')}</b>\nВыберите номинал (цена — закуп у FazerCard):",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


@router.callback_query(AdminUI.fzr_search, F.data.startswith("a_fzro:"))
async def cb_fzr_offer(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    offers, cat = data.get("offers") or [], data.get("cat") or {}
    j = int(call.data.split(":", 1)[1])
    if j >= len(offers):
        await call.answer("Список устарел, начните заново", show_alert=True)
        return
    offer, pid = offers[j], data.get("pid")
    await state.update_data(offer=offer)
    await state.set_state(AdminUI.fzr_price)
    price = offer.get("price_usd") or "?"
    await call.message.edit_text(
        f"💲 <b>{cat.get('name')} · {offer.get('name')}</b>\n"
        f"Закуп у FazerCard: <b>${price}</b>\n\n"
        "Пришлите вашу <b>цену продажи</b> в USDT (например <code>12.00</code>) "
        "или <code>-</code>, чтобы продавать по закупу.",
        reply_markup=_cancel_kb(f"a_prod:{pid}"),
    )
    await call.answer()


@router.message(AdminUI.fzr_price)
async def msg_fzr_price(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    pid, kind = data.get("pid"), data.get("kind")
    cat, offer = data.get("cat"), data.get("offer")
    if not (cat and offer):
        await state.clear()
        await message.answer("Сессия устарела, начните заново.")
        return
    raw = (message.text or "").strip().replace(",", ".")
    try:
        cost = Decimal(str(offer.get("price_usd") or "0"))
    except InvalidOperation:
        cost = Decimal("0")
    price = None
    if raw != "-":
        try:
            price = Decimal(raw)
        except InvalidOperation:
            await message.answer("Число или -")
            return
    title = f"{cat.get('name')} · {offer.get('name')}".strip(" ·")
    async with db.session() as session:
        min_vid = await session.scalar(
            select(func.min(Variant.liog_variation_id)).where(Variant.product_id == pid)
        )
        new_vid = min(0, int(min_vid or 0)) - 1
        v = Variant(
            product_id=pid, title=title[:255],
            liog_product_id=0, liog_variation_id=new_vid,
            cost_usd=cost, price_usd=price,
            source="fazercard", fzr_kind=kind,
            fzr_a=str(cat.get("id")), fzr_b=str(offer.get("id")),
        )
        session.add(v)
        await session.commit()
        vid = v.id
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ К номиналу", f"a_var:{vid}"))
    kb.row(_btn("⬅️ К товару", f"a_prod:{pid}"))
    note = ""
    if kind == "topup":
        note = (
            "\nℹ️ Топап: при покупке бот сам спросит у клиента данные игрока "
            "(напр. ID), затем оформит заказ и зачислит на аккаунт."
        )
    await message.answer(
        f"✅ Номинал FazerCard создан: <b>{title}</b>" + note,
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("a_vtitle:"))
async def cb_var_title(call: CallbackQuery, state: FSMContext) -> None:
    vid = int(call.data.split(":", 1)[1])
    await state.set_state(AdminUI.set_var_title)
    await state.update_data(vid=vid)
    await call.message.edit_text(
        "✏️ Пришлите новое <b>название номинала</b> (покороче, чтобы на телефоне "
        "было видно цену).",
        reply_markup=_cancel_kb(f"a_var:{vid}"),
    )
    await call.answer()


@router.message(AdminUI.set_var_title)
async def msg_var_title(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    vid = data.get("vid")
    title = (message.text or "").strip()
    if not title:
        await message.answer("Название пустое. Пришлите текст.")
        return
    async with db.session() as session:
        v = await session.get(Variant, vid)
        if v:
            v.title = title[:255]
            await session.commit()
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ К номиналу", f"a_var:{vid}"))
    await message.answer("✅ Название номинала обновлено", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("a_setrub:"))
async def cb_set_rub(call: CallbackQuery, state: FSMContext) -> None:
    vid = int(call.data.split(":", 1)[1])
    await state.set_state(AdminUI.set_price_rub)
    await state.update_data(vid=vid)
    await call.message.edit_text(
        "💱 Пришлите цену в рублях (например <code>199</code> или <code>199.90</code>) "
        "— она показывается рядом с ценой в $.\n"
        "<code>-</code> — убрать цену ₽ (тогда покажется только $).",
        reply_markup=_cancel_kb(f"a_var:{vid}"),
    )
    await call.answer()


@router.message(AdminUI.set_price_rub)
async def msg_set_rub(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    vid = data.get("vid")
    raw = (message.text or "").strip().replace(",", ".")
    async with db.session() as session:
        v = await session.get(Variant, vid)
        if not v:
            await state.clear()
            await message.answer("Номинал не найден")
            return
        if raw == "-":
            v.price_rub = None
        else:
            try:
                v.price_rub = Decimal(raw)
            except InvalidOperation:
                await message.answer("Число или -")
                return
        await session.commit()
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ К номиналу", f"a_var:{vid}"))
    await message.answer("✅ Цена ₽ обновлена", reply_markup=kb.as_markup())


# ── Редактируемые тексты «ожидание»/«выдача» ────────────────────────────────
# Наследование: номинал → товар → родительская категория → дефолт.
#   обычный текст — задать своё значение;
#   «0» — сбросить (наследовать с уровня выше / дефолт);
#   «-» — скрыть (пустой текст, наследование останавливается).
_TXT_FIELD = {"pend": "pending_text", "deliv": "delivered_text"}
_TXT_LABEL = {"pend": "⏳ текст ожидания", "deliv": "✅ текст выдачи"}


def _txt_default(field: str) -> str:
    return (
        texts.PENDING_NOTE_DEFAULT if field == "pending_text"
        else texts.TOPUP_ACCOUNT_DELIVERED
    )


@router.callback_query(F.data.startswith("a_txt:"))
async def cb_set_text(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    # a_txt:{kind p|v}:{field pend|deliv}:{id}
    _, kind, fkey, raw_id = call.data.split(":", 3)
    obj_id = int(raw_id)
    field = _TXT_FIELD.get(fkey)
    if field is None or kind not in ("p", "v"):
        await call.answer("Неизвестно", show_alert=True)
        return
    back = f"a_var:{obj_id}" if kind == "v" else f"a_prod:{obj_id}"
    async with db.session() as session:
        obj = await session.get(Variant if kind == "v" else Product, obj_id)
        if not obj:
            await call.answer("Не найдено", show_alert=True)
            return
        cur = getattr(obj, field, None)
    if cur is None:
        cur_line = "сейчас: <i>наследуется / по умолчанию</i>"
    elif cur == "":
        cur_line = "сейчас: <i>скрыт (ничего не отправляется)</i>"
    else:
        cur_line = f"сейчас:\n<blockquote>{cur}</blockquote>"
    await state.set_state(AdminUI.set_text)
    await state.update_data(txt_kind=kind, txt_field=field, txt_id=obj_id)
    await call.message.edit_text(
        f"✏️ Редактирование: <b>{_TXT_LABEL[fkey]}</b>\n{cur_line}\n\n"
        "Пришлите новый текст.\n"
        "<code>0</code> — сбросить (наследовать с уровня выше или дефолт).\n"
        "<code>-</code> — скрыть (ничего не отправлять покупателю).",
        reply_markup=_cancel_kb(back),
    )
    await call.answer()


@router.message(AdminUI.set_text)
async def msg_set_text(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    kind = data.get("txt_kind")
    field = data.get("txt_field")
    obj_id = data.get("txt_id")
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Пусто. Пришлите текст, либо 0 (сброс) / - (скрыть).")
        return
    if raw == "0":
        value, note = None, "сброшен (наследуется / по умолчанию)"
    elif raw == "-":
        value, note = "", "скрыт — покупателю ничего не отправляется"
    else:
        value, note = raw, "обновлён"
    back = f"a_var:{obj_id}" if kind == "v" else f"a_prod:{obj_id}"
    async with db.session() as session:
        obj = await session.get(Variant if kind == "v" else Product, obj_id)
        if not obj:
            await state.clear()
            await message.answer("Объект не найден")
            return
        setattr(obj, field, value)
        await session.commit()
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ Назад", back))
    await message.answer(f"✅ Текст {note}", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("a_setprice:"))
async def cb_set_price(call: CallbackQuery, state: FSMContext) -> None:
    vid = int(call.data.split(":", 1)[1])
    await state.set_state(AdminUI.set_price)
    await state.update_data(vid=vid)
    await call.message.edit_text(
        "💲 Пришлите фикс-цену в USDT (например <code>5.50</code>) "
        "или <code>-</code>, чтобы убрать (считать по наценке).",
        reply_markup=_cancel_kb(f"a_var:{vid}"),
    )
    await call.answer()


@router.message(AdminUI.set_price)
async def msg_set_price(message: Message, db: Database, config: BotConfig, state: FSMContext) -> None:
    data = await state.get_data()
    vid = data.get("vid")
    raw = (message.text or "").strip().replace(",", ".")
    async with db.session() as session:
        v = await session.get(Variant, vid)
        if not v:
            await state.clear()
            await message.answer("Номинал не найден")
            return
        if raw == "-":
            v.price_usd = None
        else:
            try:
                v.price_usd = Decimal(raw)
            except InvalidOperation:
                await message.answer("Число или -")
                return
        await session.commit()
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ К номиналу", f"a_var:{vid}"))
    await message.answer("✅ Цена обновлена", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("a_setmarkup:"))
async def cb_set_markup(call: CallbackQuery, state: FSMContext) -> None:
    vid = int(call.data.split(":", 1)[1])
    await state.set_state(AdminUI.set_markup)
    await state.update_data(vid=vid)
    await call.message.edit_text(
        "📈 Пришлите наценку в % (например <code>20</code>) "
        "или <code>-</code>, чтобы вернуть наценку по умолчанию.",
        reply_markup=_cancel_kb(f"a_var:{vid}"),
    )
    await call.answer()


@router.message(AdminUI.set_markup)
async def msg_set_markup(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    vid = data.get("vid")
    raw = (message.text or "").strip().replace(",", ".")
    async with db.session() as session:
        v = await session.get(Variant, vid)
        if not v:
            await state.clear()
            await message.answer("Номинал не найден")
            return
        if raw == "-":
            v.markup_percent = None
        else:
            try:
                v.markup_percent = Decimal(raw)
            except InvalidOperation:
                await message.answer("Число или -")
                return
        await session.commit()
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ К номиналу", f"a_var:{vid}"))
    await message.answer("✅ Наценка обновлена", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("a_astock:"))
async def cb_add_stock(call: CallbackQuery, state: FSMContext) -> None:
    vid = int(call.data.split(":", 1)[1])
    await state.set_state(AdminUI.add_stock)
    await state.update_data(vid=vid)
    await call.message.edit_text(
        "📥 Пришлите коды для стока — по одному в строке.",
        reply_markup=_cancel_kb(f"a_var:{vid}"),
    )
    await call.answer()


@router.message(AdminUI.add_stock)
async def msg_add_stock(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    vid = data.get("vid")
    codes = [ln for ln in (message.text or "").splitlines() if ln.strip()]
    if not codes:
        await message.answer("Пусто. Пришлите коды построчно или нажмите Отмена.")
        return
    async with db.session() as session:
        added, skipped = await stock_service.add_codes(session, vid, codes)
        await session.commit()
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ К номиналу", f"a_var:{vid}"))
    await message.answer(
        f"✅ Добавлено: {added}" + (f", пропущено дублей: {skipped}" if skipped else ""),
        reply_markup=kb.as_markup(),
    )


# ── Эмодзи товара/номинала ───────────────────────────────────────────────────
_EMOJI_HINT = (
    "🙂 Пришлите <b>одно премиум-эмодзи</b> (нужен Telegram Premium) "
    "или его <b>ID</b> числом (например <code>5406705291213417505</code>), "
    "чтобы поставить иконкой кнопки, или <code>-</code> чтобы убрать.\n"
    "Обычные (не премиум) эмодзи иконкой кнопки Telegram не принимает."
)


@router.callback_query(F.data.startswith("a_pemoji:"))
async def cb_prod_emoji(call: CallbackQuery, state: FSMContext) -> None:
    pid = int(call.data.split(":", 1)[1])
    await state.set_state(AdminUI.set_prod_emoji)
    await state.update_data(pid=pid)
    await call.message.edit_text(_EMOJI_HINT, reply_markup=_cancel_kb(f"a_prod:{pid}"))
    await call.answer()


@router.message(AdminUI.set_prod_emoji)
async def msg_prod_emoji(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    pid = data.get("pid")
    raw = (message.text or "").strip()
    emoji_id = None if raw == "-" else _emoji_id_from(message)
    if raw != "-" and emoji_id is None:
        await message.answer("Не нашёл эмодзи. Пришлите премиум-эмодзи, его ID числом или - ")
        return
    async with db.session() as session:
        p = await session.get(Product, pid)
        if p:
            p.icon_emoji_id = emoji_id
            await session.commit()
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ К товару", f"a_prod:{pid}"))
    await message.answer("✅ Эмодзи обновлён" if emoji_id else "✅ Эмодзи убран", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("a_vemoji:"))
async def cb_var_emoji(call: CallbackQuery, state: FSMContext) -> None:
    vid = int(call.data.split(":", 1)[1])
    await state.set_state(AdminUI.set_var_emoji)
    await state.update_data(vid=vid)
    await call.message.edit_text(_EMOJI_HINT, reply_markup=_cancel_kb(f"a_var:{vid}"))
    await call.answer()


@router.message(AdminUI.set_var_emoji)
async def msg_var_emoji(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    vid = data.get("vid")
    raw = (message.text or "").strip()
    emoji_id = None if raw == "-" else _emoji_id_from(message)
    if raw != "-" and emoji_id is None:
        await message.answer("Не нашёл эмодзи. Пришлите премиум-эмодзи, его ID числом или - ")
        return
    async with db.session() as session:
        v = await session.get(Variant, vid)
        if v:
            v.icon_emoji_id = emoji_id
            await session.commit()
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ К номиналу", f"a_var:{vid}"))
    await message.answer("✅ Эмодзи обновлён" if emoji_id else "✅ Эмодзи убран", reply_markup=kb.as_markup())


# ── Заказы / сток ────────────────────────────────────────────────────────────
@router.callback_query(F.data == "a_orders")
async def cb_orders(call: CallbackQuery, db: Database, config: BotConfig) -> None:
    async with db.session() as session:
        rows = await session.execute(
            select(Order, Variant.title, User.username)
            .join(Variant, Variant.id == Order.variant_id)
            .join(User, User.id == Order.user_id, isouter=True)
            .order_by(Order.id.desc())
            .limit(15)
        )
        items = rows.all()
    if not items:
        text = "🧾 Заказов пока нет."
    else:
        lines = ["🧾 <b>Последние заказы</b>", ""]
        for o, vtitle, username in items:
            total = Decimal(o.price_usd) * (o.quantity or 1)
            who = f"@{username}" if username else f"id {o.user_id}"
            lines.append(
                f"#{o.id} · {vtitle} ×{o.quantity or 1} · "
                f"{texts.money(total, config.currency)} · {o.status} · {who} (<code>{o.user_id}</code>)"
            )
        text = "\n".join(lines)
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ Назад", "a_home"))
    await call.message.edit_text(text, reply_markup=kb.as_markup())
    await call.answer()


@router.callback_query(F.data == "a_stock")
async def cb_stock(call: CallbackQuery, db: Database) -> None:
    async with db.session() as session:
        rows = await session.execute(
            select(Variant.title, func.count(StockItem.id))
            .select_from(Variant)
            .join(
                StockItem,
                (StockItem.variant_id == Variant.id)
                & (StockItem.status == StockStatus.AVAILABLE),
                isouter=True,
            )
            .group_by(Variant.id)
            .order_by(Variant.id)
        )
        items = rows.all()
    if not items:
        text = "📥 Номиналов нет."
    else:
        lines = ["📥 <b>Сток (доступно)</b>", ""]
        for title, cnt in items:
            lines.append(f"{title}: <b>{cnt or 0}</b>")
        text = "\n".join(lines)
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ Назад", "a_home"))
    await call.message.edit_text(text, reply_markup=kb.as_markup())
    await call.answer()
