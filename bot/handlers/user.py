"""Пользовательские хендлеры: каталог, покупка, оплата, выдача."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from ..config import BotConfig
from ..db import Database
from ..db.models import Order, OrderStatus, Payment, PaymentStatus
from ..payments import PaymentProvider
from ..services import catalog as catalog_service
from ..services import orders as order_service
from ..services import stock as stock_service
from ..services.pricing import sale_price
from .. import keyboards as kb
from .. import texts

from liogames import LioGamesClient

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database) -> None:
    async with db.session() as session:
        await order_service.ensure_user(
            session,
            user_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
        await session.commit()
        products = await catalog_service.list_products(session)
    text = texts.START
    if not products:
        await message.answer(text + "\n\n" + texts.CATALOG_EMPTY)
    else:
        await message.answer(text)
        await message.answer(texts.CHOOSE_PRODUCT, reply_markup=kb.products_kb(products))


@router.callback_query(F.data == "catalog")
async def cb_catalog(call: CallbackQuery, db: Database) -> None:
    async with db.session() as session:
        products = await catalog_service.list_products(session)
    if not products:
        await call.message.edit_text(texts.CATALOG_EMPTY)
    else:
        await call.message.edit_text(
            texts.CHOOSE_PRODUCT, reply_markup=kb.products_kb(products)
        )
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
        prices = {
            v.id: sale_price(v, config.default_markup_percent) for v in variants
        }
        stock = await stock_service.counts_by_variant(session, [v.id for v in variants])

    if not variants:
        await call.answer("Нет доступных номиналов", show_alert=True)
        return

    header = f"<b>{product.title}</b>"
    if product.description:
        header += f"\n{product.description}"
    header += "\n\nВыберите номинал:"
    await call.message.edit_text(
        header,
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

    note = "в наличии ✅" if in_stock > 0 else texts.OUT_OF_STOCK_NOTE
    text = (
        f"<b>{variant.title}</b>\n"
        f"Цена: <b>{texts.money(price, config.currency)}</b>\n"
        f"Статус: {note}"
    )
    await call.message.edit_text(text, reply_markup=kb.buy_kb(variant_id))
    await call.answer()


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(
    call: CallbackQuery, db: Database, config: BotConfig, provider: PaymentProvider
) -> None:
    variant_id = int(call.data.split(":", 1)[1])
    async with db.session() as session:
        variant = await catalog_service.get_variant(session, variant_id)
        if not variant or not variant.is_active:
            await call.answer("Недоступно", show_alert=True)
            return
        price = sale_price(variant, config.default_markup_percent)

        if price < Decimal("1.00"):
            await call.answer(
                "Минимальная сумма оплаты — 1 USDT. Обратитесь к продавцу.",
                show_alert=True,
            )
            return

        await order_service.ensure_user(
            session,
            user_id=call.from_user.id,
            username=call.from_user.username,
            full_name=call.from_user.full_name,
        )
        order = await order_service.create_order(
            session, user_id=call.from_user.id, variant=variant, price_usd=price
        )

        invoice = await provider.create_invoice(
            amount=price,
            client_ref=order.client_ref,
            description=f"{variant.product.title if variant.product else ''} {variant.title}".strip(),
            notify_url=config.notify_url,
            success_url=config.public_base_url,
        )
        payment = Payment(
            order_id=order.id,
            provider=provider.name,
            provider_order_id=invoice.provider_order_id,
            checkout_url=invoice.checkout_url,
            address=invoice.address,
            network=invoice.network,
            amount=invoice.amount,
            currency=invoice.currency,
            status=PaymentStatus.PENDING,
            expires_at=invoice.expires_at,
        )
        order.status = OrderStatus.AWAITING_PAYMENT
        session.add(payment)
        await session.commit()

        item_name = variant.title

    await call.message.edit_text(
        texts.PAYMENT_CREATED.format(
            item=item_name,
            amount=texts.money(invoice.amount, invoice.currency),
            network=invoice.network,
            address=invoice.address,
        ),
        reply_markup=kb.payment_kb(order.id, invoice.checkout_url),
    )
    await call.answer()


@router.callback_query(F.data.startswith("check:"))
async def cb_check(
    call: CallbackQuery,
    db: Database,
    config: BotConfig,
    provider: PaymentProvider,
    liog: LioGamesClient,
) -> None:
    order_id = int(call.data.split(":", 1)[1])
    async with db.session() as session:
        order = await session.get(Order, order_id)
        if not order or order.user_id != call.from_user.id:
            await call.answer("Заказ не найден", show_alert=True)
            return

        if order.status == OrderStatus.COMPLETED and order.delivery_code:
            await call.message.answer(
                texts.DELIVERY_SUCCESS.format(code=order.delivery_code)
            )
            await call.answer()
            return

        payment = order.payment
        if payment is None:
            await call.answer("Счёт не найден", show_alert=True)
            return

        # Просрочка?
        if (
            payment.expires_at
            and datetime.utcnow() > payment.expires_at
            and payment.status == PaymentStatus.PENDING
        ):
            payment.status = PaymentStatus.EXPIRED
            order.status = OrderStatus.EXPIRED
            await session.commit()
            await call.message.edit_text(texts.PAYMENT_EXPIRED)
            await call.answer()
            return

        update = await provider.get_status(order.client_ref)
        if update.status != "paid":
            await session.commit()
            await call.answer(texts.PAYMENT_PENDING, show_alert=True)
            return

        # Оплачено -> фиксируем и выдаём.
        payment.status = PaymentStatus.PAID
        payment.tx_hash = update.tx_hash
        payment.paid_at = datetime.utcnow()
        order.status = OrderStatus.PAID
        await session.flush()

        variant = await catalog_service.get_variant(session, order.variant_id)
        status = await order_service.fulfill(session, order, variant, liog)
        await session.commit()

        if status == OrderStatus.COMPLETED and order.delivery_code:
            await call.message.edit_text(
                texts.DELIVERY_SUCCESS.format(code=order.delivery_code)
            )
        elif status == OrderStatus.FULFILLING:
            await call.message.edit_text(texts.FULFILLING)
        else:
            await call.message.edit_text(
                texts.FULFILL_FAILED.format(ref=order.client_ref)
            )
    await call.answer()
