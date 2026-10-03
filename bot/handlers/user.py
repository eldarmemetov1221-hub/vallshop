"""Пользовательские хендлеры: каталог и покупка с баланса (с количеством).

Все экраны — фото-баннер с подписью и inline-кнопками (см. bot.ui).
"""

from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from ..config import BotConfig
from ..db import Database
from ..services import catalog as catalog_service
from ..services import orders as order_service
from ..services import stock as stock_service
from ..services.balance import get_balance
from ..services.orders import InsufficientBalance, OutOfStock
from ..services.pricing import sale_price
from ..ui import render
from .. import keyboards as kb
from .. import texts

router = Router()

MAX_QTY_CAP = 50  # верхний предел количества за одну покупку


async def _clear_reply_keyboard(message: Message) -> None:
    """Убрать старую нижнюю reply-клавиатуру (всё меню теперь inline)."""
    try:
        m = await message.answer("⌛", reply_markup=ReplyKeyboardRemove())
        await m.delete()
    except Exception:  # noqa: BLE001
        pass


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database, config: BotConfig) -> None:
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
    await render(message, banner="catalog", caption=texts.START, reply_markup=kb.main_menu_kb(is_admin))


@router.message(Command("menu"))
async def cmd_menu(message: Message, config: BotConfig) -> None:
    is_admin = config.is_admin(message.from_user.id)
    await render(message, banner="catalog", caption=texts.START, reply_markup=kb.main_menu_kb(is_admin))


@router.message(F.text == "🛍 Каталог")
async def msg_catalog(message: Message, db: Database) -> None:
    async with db.session() as session:
        products = await catalog_service.list_products(session)
    caption = texts.CHOOSE_PRODUCT if products else texts.CATALOG_EMPTY
    await render(message, banner="catalog", caption=caption, reply_markup=kb.products_kb(products))


@router.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery, config: BotConfig) -> None:
    is_admin = config.is_admin(call.from_user.id)
    await render(call, banner="catalog", caption=texts.START, reply_markup=kb.main_menu_kb(is_admin))
    await call.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


@router.callback_query(F.data == "catalog")
async def cb_catalog(call: CallbackQuery, db: Database) -> None:
    async with db.session() as session:
        products = await catalog_service.list_products(session)
    caption = texts.CHOOSE_PRODUCT if products else texts.CATALOG_EMPTY
    await render(call, banner="catalog", caption=caption, reply_markup=kb.products_kb(products))
    await call.answer()


@router.callback_query(F.data.startswith("prod:"))
async def cb_product(call: CallbackQuery, db: Database, config: BotConfig) -> None:
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

    prod_emoji = texts.ce(product.icon_emoji_id or "5298953332079999355", "🎮")
    caption = f"{prod_emoji} <b>{product.title}</b>"
    if product.description:
        caption += f"\n{product.description}"
    caption += "\n\nВыберите номинал:"
    await render(
        call,
        banner="catalog",
        caption=caption,
        reply_markup=kb.variants_kb(variants, prices, stock, config.currency),
    )
    await call.answer()


@router.callback_query(F.data.startswith("var:"))
async def cb_variant(call: CallbackQuery, db: Database, config: BotConfig) -> None:
    variant_id = int(call.data.split(":", 1)[1])
    async with db.session() as session:
        variant = await catalog_service.get_variant(session, variant_id)
        if not variant or not variant.is_active:
            await call.answer("Недоступно", show_alert=True)
            return
        price = sale_price(variant, config.default_markup_percent)
        in_stock = await stock_service.available_count(session, variant_id)

    note = f"в наличии: {in_stock} шт ✅" if in_stock > 0 else texts.OUT_OF_STOCK_NOTE
    caption = (
        f"<b>{variant.title}</b>\n"
        f"Цена: <b>{texts.money(price, config.currency)}</b>\n"
        f"Статус: {note}"
    )
    await render(call, banner="catalog", caption=caption, reply_markup=kb.buy_kb(variant_id))
    await call.answer()


async def _render_quantity(
    call: CallbackQuery, db: Database, config: BotConfig, variant_id: int, qty: int
) -> None:
    async with db.session() as session:
        variant = await catalog_service.get_variant(session, variant_id)
        if not variant or not variant.is_active:
            await call.answer("Недоступно", show_alert=True)
            return
        price = sale_price(variant, config.default_markup_percent)
        in_stock = await stock_service.available_count(session, variant_id)
        balance = await get_balance(session, call.from_user.id)

    if in_stock <= 0:
        await render(
            call, banner="catalog", caption=texts.OUT_OF_STOCK_FULL,
            reply_markup=kb.buy_kb(variant_id),
        )
        await call.answer()
        return

    max_qty = min(in_stock, MAX_QTY_CAP)
    qty = max(1, min(qty, max_qty))
    total = price * qty

    await render(
        call,
        banner="catalog",
        caption=texts.CHOOSE_QUANTITY.format(
            item=variant.title,
            price=texts.money(price, config.currency),
            stock=in_stock,
            balance=texts.money(balance, config.currency),
        ),
        reply_markup=kb.quantity_kb(variant_id, qty, total, config.currency, max_qty),
    )
    await call.answer()


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(call: CallbackQuery, db: Database, config: BotConfig) -> None:
    variant_id = int(call.data.split(":", 1)[1])
    await _render_quantity(call, db, config, variant_id, 1)


@router.callback_query(F.data.startswith("qty:"))
async def cb_qty(call: CallbackQuery, db: Database, config: BotConfig) -> None:
    _, vid, n = call.data.split(":", 2)
    await _render_quantity(call, db, config, int(vid), int(n))


@router.callback_query(F.data.startswith("confirm:"))
async def cb_confirm(call: CallbackQuery, db: Database, config: BotConfig) -> None:
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

        await session.commit()
        total = unit_price * qty
        balance = await get_balance(session, call.from_user.id)
        item_name = variant.title

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
