"""Пользовательские хендлеры: каталог и покупка с баланса (с количеством).

Все экраны — фото-баннер с подписью и inline-кнопками (см. bot.ui).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..config import BotConfig
from ..db import Database
from ..services import catalog as catalog_service
from ..services import orders as order_service
from ..services import stock as stock_service
from ..services.balance import get_balance
from ..services.orders import InsufficientBalance, OutOfStock, SupplierError
from ..services.pricing import sale_price
from ..db.models import OrderStatus
from fazercard import FazerCardClient
from ..ui import render
from .. import keyboards as kb
from .. import texts

router = Router()

MAX_QTY_CAP = 50  # верхний предел количества за одну покупку


class BuyFlow(StatesGroup):
    """Покупка топапа FazerCard: пошаговый сбор данных игрока."""
    collecting = State()
    confirming = State()


async def _clear_reply_keyboard(message: Message) -> None:
    """Убрать старую нижнюю reply-клавиатуру (всё меню теперь inline)."""
    try:
        m = await message.answer("⌛", reply_markup=ReplyKeyboardRemove())
        await m.delete()
    except Exception:  # noqa: BLE001
        pass


@router.message(CommandStart())
async def cmd_start(
    message: Message, db: Database, config: BotConfig, state: FSMContext
) -> None:
    await state.clear()
    async with db.session() as session:
        await order_service.ensure_user(
            session,
            user_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
        await session.commit()
    await _clear_reply_keyboard(message)
    is_admin = config.is_admin(message.from_user.id)
    await render(message, banner="main", caption=texts.START, reply_markup=kb.main_menu_kb(is_admin))


@router.message(Command("menu"))
async def cmd_menu(message: Message, config: BotConfig) -> None:
    is_admin = config.is_admin(message.from_user.id)
    await render(message, banner="main", caption=texts.START, reply_markup=kb.main_menu_kb(is_admin))


@router.message(F.text == "🛍 Каталог")
async def msg_catalog(message: Message, db: Database) -> None:
    async with db.session() as session:
        products = await catalog_service.list_products(session)
    caption = texts.CHOOSE_PRODUCT if products else texts.CATALOG_EMPTY
    await render(message, banner="catalog", caption=caption, reply_markup=kb.products_kb(products))


@router.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery, config: BotConfig) -> None:
    is_admin = config.is_admin(call.from_user.id)
    await render(call, banner="main", caption=texts.START, reply_markup=kb.main_menu_kb(is_admin))
    await call.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


@router.callback_query(F.data == "faq")
async def cb_faq(call: CallbackQuery, config: BotConfig, state: FSMContext) -> None:
    await state.clear()
    caption = texts.FAQ
    if not config.offer_url and config.support_contact:
        caption += texts.FAQ_NO_OFFER.format(support=config.support_contact)
    await render(
        call, banner="main", caption=caption,
        reply_markup=kb.faq_kb(
            config.offer_url, config.agreement_url, config.privacy_url
        ),
    )
    await call.answer()


@router.callback_query(F.data == "catalog")
async def cb_catalog(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    async with db.session() as session:
        products = await catalog_service.list_products(session)
    caption = texts.CHOOSE_PRODUCT if products else texts.CATALOG_EMPTY
    await render(call, banner="catalog", caption=caption, reply_markup=kb.products_kb(products))
    await call.answer()


@router.callback_query(F.data.startswith("prod:"))
async def cb_product(
    call: CallbackQuery, db: Database, config: BotConfig, fzr: FazerCardClient
) -> None:
    product_id = int(call.data.split(":", 1)[1])
    async with db.session() as session:
        product = await catalog_service.get_product(session, product_id)
        if not product:
            await call.answer("Товар не найден", show_alert=True)
            return
        variants = await catalog_service.list_variants(session, product_id)
        prices = {v.id: sale_price(v, config.default_markup_percent) for v in variants}
        stock = await stock_service.counts_by_variant(session, [v.id for v in variants])

    if not variants:
        await call.answer("Нет доступных номиналов", show_alert=True)
        return

    # Живой сток FazerCard (под заказ — только у LioGames/своего стока).
    stock = dict(stock)
    stock.update(await catalog_service.fazercard_stock(fzr, variants))

    prod_emoji = texts.ce(product.icon_emoji_id or "5298953332079999355", "🎮")
    caption = f"{prod_emoji} <b>{product.title}</b>"
    if product.description:
        caption += f"\n{product.description}"
    caption += "\n\nВыберите номинал:"
    await render(
        call,
        banner="catalog",
        caption=caption,
        reply_markup=kb.variants_kb(
            variants, prices, stock, config.currency,
            product_icon=product.icon_emoji_id,
        ),
    )
    await call.answer()


@router.callback_query(F.data.startswith("var:"))
async def cb_variant(
    call: CallbackQuery, db: Database, config: BotConfig, fzr: FazerCardClient,
    state: FSMContext,
) -> None:
    await state.clear()  # сбросить незавершённый сбор данных топапа (в т.ч. отмену)
    variant_id = int(call.data.split(":", 1)[1])
    async with db.session() as session:
        variant = await catalog_service.get_variant(session, variant_id)
        if not variant or not variant.is_active:
            await call.answer("Недоступно", show_alert=True)
            return
        price = sale_price(variant, config.default_markup_percent)
        in_stock = await stock_service.available_count(session, variant_id)
        ondemand = variant.source == "fazercard"
        if ondemand:
            fz = await catalog_service.fazercard_stock(fzr, [variant])
            in_stock = fz.get(variant.id)  # int (реальный сток) или None (неизвестно)

    if ondemand:
        if in_stock is None:
            note = "в наличии ✅ (выдача за пару минут)"
        elif in_stock > 0:
            note = f"в наличии: {in_stock} шт ✅"
        else:
            note = "❌ нет в наличии"
    elif in_stock > 0:
        note = f"в наличии: {in_stock} шт ✅"
    else:
        note = texts.OUT_OF_STOCK_NOTE
    caption = (
        f"<b>{variant.title}</b>\n"
        f"Цена: <b>{texts.money(price, config.currency)}</b>\n"
        f"Статус: {note}"
    )
    await render(call, banner="catalog", caption=caption, reply_markup=kb.buy_kb(variant_id))
    await call.answer()


async def _render_quantity(
    call: CallbackQuery, db: Database, config: BotConfig, fzr: FazerCardClient,
    variant_id: int, qty: int,
) -> None:
    async with db.session() as session:
        variant = await catalog_service.get_variant(session, variant_id)
        if not variant or not variant.is_active:
            await call.answer("Недоступно", show_alert=True)
            return
        price = sale_price(variant, config.default_markup_percent)
        in_stock = await stock_service.available_count(session, variant_id)
        balance = await get_balance(session, call.from_user.id)
        ondemand = variant.source == "fazercard"
        live = None
        if ondemand:
            fz = await catalog_service.fazercard_stock(fzr, [variant])
            live = fz.get(variant.id)  # int или None (неизвестно)

    if ondemand:
        # Неизвестный сток трактуем как доступный (ограничим общим капом).
        avail = MAX_QTY_CAP if live is None else live
    else:
        avail = in_stock

    if avail <= 0:
        await render(
            call, banner="catalog", caption=texts.OUT_OF_STOCK_FULL,
            reply_markup=kb.buy_kb(variant_id),
        )
        await call.answer()
        return

    max_qty = min(avail, MAX_QTY_CAP)
    qty = max(1, min(qty, max_qty))
    total = price * qty
    if ondemand:
        stock_label = "—" if live is None else live
    else:
        stock_label = in_stock

    await render(
        call,
        banner="catalog",
        caption=texts.CHOOSE_QUANTITY.format(
            item=variant.title,
            price=texts.money(price, config.currency),
            stock=stock_label,
            balance=texts.money(balance, config.currency),
        ),
        reply_markup=kb.quantity_kb(variant_id, qty, total, config.currency, max_qty),
    )
    await call.answer()


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(
    call: CallbackQuery, db: Database, config: BotConfig, fzr: FazerCardClient,
    state: FSMContext,
) -> None:
    variant_id = int(call.data.split(":", 1)[1])
    async with db.session() as session:
        variant = await catalog_service.get_variant(session, variant_id)
        is_topup = bool(
            variant and variant.is_active
            and variant.source == "fazercard" and variant.fzr_kind == "topup"
        )
    if is_topup:
        await _start_topup(call, config, fzr, state, variant)
        return
    await _render_quantity(call, db, config, fzr, variant_id, 1)


@router.callback_query(F.data.startswith("qty:"))
async def cb_qty(
    call: CallbackQuery, db: Database, config: BotConfig, fzr: FazerCardClient
) -> None:
    _, vid, n = call.data.split(":", 2)
    await _render_quantity(call, db, config, fzr, int(vid), int(n))


# ── Покупка топапа: сбор данных игрока (fields) ──────────────────────────────
async def _start_topup(call, config, fzr, state, variant) -> None:
    price = sale_price(variant, config.default_markup_percent)
    try:
        meta = await asyncio.to_thread(fzr.topup_meta, variant.fzr_a)
    except Exception:  # noqa: BLE001
        await call.answer("Поставщик недоступен, попробуйте позже", show_alert=True)
        return
    fields = meta.get("fields") or []
    await state.set_state(BuyFlow.collecting)
    await state.update_data(
        vid=variant.id, fields=fields, idx=0, answers={},
        price_str=texts.money(price, config.currency), title=variant.title,
    )
    intro = f"🧩 <b>{variant.title}</b>\nЦена: <b>{texts.money(price, config.currency)}</b>"
    if meta.get("note"):
        intro += f"\n\nℹ️ {meta['note']}"
    await call.message.answer(intro)
    await _ask_next(call.message, state)
    await call.answer()


def _field_cancel_kb(vid: int) -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"var:{vid}"))
    return b


async def _ask_next(target: Message, state: FSMContext) -> None:
    data = await state.get_data()
    fields, idx = data["fields"], data["idx"]
    if idx >= len(fields):
        await _show_topup_confirm(target, state)
        return
    f = fields[idx]
    label = f.get("label") or f.get("key")
    ftype = (f.get("type") or "text").lower()
    opts = f.get("options") or []
    await state.set_state(BuyFlow.collecting)
    if ftype == "select" and opts:
        b = InlineKeyboardBuilder()
        for i, o in enumerate(opts):
            b.row(InlineKeyboardButton(text=str(o), callback_data=f"fsel:{i}"))
        b.row(InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"var:{data['vid']}"))
        await target.answer(f"Выберите «{label}»:", reply_markup=b.as_markup())
    else:
        await target.answer(
            f"Введите «{label}»:", reply_markup=_field_cancel_kb(data["vid"]).as_markup()
        )


async def _show_topup_confirm(target: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lines = [f"🧾 <b>{data['title']}</b>", f"Цена: <b>{data['price_str']}</b>", "", "Данные:"]
    for f in data["fields"]:
        val = data["answers"].get(f["key"], "")
        lines.append(f"• {f.get('label') or f.get('key')}: <code>{val}</code>")
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=f"✅ Купить за {data['price_str']}", callback_data="tbuy"))
    b.row(InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"var:{data['vid']}"))
    await state.set_state(BuyFlow.confirming)
    await target.answer("\n".join(lines), reply_markup=b.as_markup())


@router.message(BuyFlow.collecting)
async def msg_collect_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    fields, idx = data["fields"], data["idx"]
    if idx >= len(fields):
        await _show_topup_confirm(message, state)
        return
    f = fields[idx]
    if (f.get("type") or "text").lower() == "select" and (f.get("options") or []):
        await message.answer("Пожалуйста, выберите вариант кнопкой выше.")
        return
    val = (message.text or "").strip()
    if not val:
        await message.answer("Пустое значение. Введите ещё раз.")
        return
    answers = dict(data["answers"])
    answers[f["key"]] = val
    await state.update_data(answers=answers, idx=idx + 1)
    await _ask_next(message, state)


@router.callback_query(BuyFlow.collecting, F.data.startswith("fsel:"))
async def cb_select_field(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    fields, idx = data["fields"], data["idx"]
    if idx >= len(fields):
        await call.answer()
        return
    opts = fields[idx].get("options") or []
    i = int(call.data.split(":", 1)[1])
    if i >= len(opts):
        await call.answer("Список устарел", show_alert=True)
        return
    answers = dict(data["answers"])
    answers[fields[idx]["key"]] = str(opts[i])
    await state.update_data(answers=answers, idx=idx + 1)
    await call.answer()
    await _ask_next(call.message, state)


@router.callback_query(BuyFlow.confirming, F.data == "tbuy")
async def cb_topup_confirm(
    call: CallbackQuery, db: Database, config: BotConfig, fzr: FazerCardClient,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    vid, answers = data["vid"], data["answers"]
    async with db.session() as session:
        variant = await catalog_service.get_variant(session, vid)
        if not variant or not variant.is_active:
            await state.clear()
            await call.answer("Недоступно", show_alert=True)
            return
        unit_price = sale_price(variant, config.default_markup_percent)
        await order_service.ensure_user(
            session, call.from_user.id, call.from_user.username, call.from_user.full_name
        )
        try:
            order, codes = await order_service.purchase_from_balance(
                session, user_id=call.from_user.id, variant=variant,
                unit_price=unit_price, quantity=1, fzr=fzr, topup_fields=answers,
            )
        except InsufficientBalance:
            await session.rollback()
            bal = await get_balance(session, call.from_user.id)
            await call.answer(
                texts.NOT_ENOUGH_BALANCE.format(
                    total=texts.money(unit_price, config.currency),
                    balance=texts.money(bal, config.currency),
                ),
                show_alert=True,
            )
            return
        except SupplierError:
            await session.rollback()
            await call.answer(
                "😔 Поставщик временно недоступен, деньги не списаны.", show_alert=True
            )
            return
        status = order.status
        await session.commit()
        total = unit_price
        balance = await get_balance(session, call.from_user.id)
        item_name = variant.title

    await state.clear()
    bal_str = texts.money(balance, config.currency)
    total_str = texts.money(total, config.currency)
    if status == OrderStatus.COMPLETED and codes:
        codes_text = "\n".join(f"<code>{c}</code>" for c in codes)
        await call.message.answer(
            texts.PURCHASE_SUCCESS.format(
                item=item_name, qty=1, total=total_str, balance=bal_str, codes=codes_text
            ),
            reply_markup=kb.after_purchase_kb(),
        )
    elif status == OrderStatus.COMPLETED:
        await call.message.answer(
            texts.TOPUP_ACCOUNT_DELIVERED, reply_markup=kb.after_purchase_kb()
        )
    else:  # FULFILLING
        await call.message.answer(
            texts.PURCHASE_PENDING.format(
                item=item_name, qty=1, total=total_str, balance=bal_str
            ),
            reply_markup=kb.after_purchase_kb(),
        )
    await call.answer("Готово ✅")


@router.callback_query(F.data.startswith("confirm:"))
async def cb_confirm(
    call: CallbackQuery, db: Database, config: BotConfig, fzr: FazerCardClient
) -> None:
    _, vid, n = call.data.split(":", 2)
    variant_id, qty = int(vid), int(n)

    async with db.session() as session:
        variant = await catalog_service.get_variant(session, variant_id)
        if not variant or not variant.is_active:
            await call.answer("Недоступно", show_alert=True)
            return
        unit_price = sale_price(variant, config.default_markup_percent)
        await order_service.ensure_user(
            session,
            user_id=call.from_user.id,
            username=call.from_user.username,
            full_name=call.from_user.full_name,
        )
        try:
            order, codes = await order_service.purchase_from_balance(
                session,
                user_id=call.from_user.id,
                variant=variant,
                unit_price=unit_price,
                quantity=qty,
                fzr=fzr,
            )
        except InsufficientBalance:
            await session.rollback()
            balance = await get_balance(session, call.from_user.id)
            await call.answer(
                texts.NOT_ENOUGH_BALANCE.format(
                    total=texts.money(unit_price * qty, config.currency),
                    balance=texts.money(balance, config.currency),
                ),
                show_alert=True,
            )
            return
        except OutOfStock:
            await session.rollback()
            in_stock = await stock_service.available_count(session, variant_id)
            await call.answer(
                texts.NOT_ENOUGH_STOCK.format(stock=in_stock), show_alert=True
            )
            return
        except SupplierError:
            await session.rollback()
            await call.answer(
                "😔 Поставщик временно недоступен, деньги не списаны. "
                "Попробуйте позже.",
                show_alert=True,
            )
            return

        pending = order.status == OrderStatus.FULFILLING and not codes
        await session.commit()
        total = unit_price * qty
        balance = await get_balance(session, call.from_user.id)
        item_name = variant.title

    if pending:
        await render(
            call,
            banner="catalog",
            caption=texts.PURCHASE_PENDING.format(
                item=item_name,
                qty=qty,
                total=texts.money(total, config.currency),
                balance=texts.money(balance, config.currency),
            ),
            reply_markup=kb.after_purchase_kb(),
        )
        await call.answer("Оформляем ⏳")
        return

    codes_text = "\n".join(f"<code>{c}</code>" for c in codes)
    await render(
        call,
        banner="catalog",
        caption=texts.PURCHASE_SUCCESS.format(
            item=item_name,
            qty=qty,
            total=texts.money(total, config.currency),
            balance=texts.money(balance, config.currency),
            codes=codes_text,
        ),
        reply_markup=kb.after_purchase_kb(),
    )
    await call.answer("Готово ✅")
