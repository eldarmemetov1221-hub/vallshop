"""Профиль покупателя: баланс, пополнение (крипта), заказы, история пополнений.

Все экраны — фото-баннер «Профиль» с подписью и inline-кнопками (см. bot.ui).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from sqlalchemy import func, select

from ..config import BotConfig
from ..db import Database
from ..db.models import Order, OrderStatus, TopUp, TopUpStatus, Variant
from ..payments import PaymentProvider
from ..services import balance as balance_service
from ..services import orders as order_service
from ..services.balance import TOPUP_MAX, TOPUP_MIN
from ..ui import render
from .. import keyboards as kb
from .. import texts

router = Router()


class TopUpFlow(StatesGroup):
    waiting_amount = State()
    waiting_network = State()


def _status_ru(status: str) -> str:
    return {
        "completed": "✅ выполнен",
        "created": "🕒 создаётся",
        "awaiting_payment": "⏳ ожидает оплату",
        "paid": "💳 оплачен",
        "fulfilling": "⏳ выдаётся",
        "failed": "⚠️ ошибка",
        "expired": "❌ просрочен",
        "refunded": "↩️ возврат",
        "pending": "⏳ ожидает",
    }.get(str(status), str(status))


async def _profile_caption(session, config: BotConfig, user_id: int) -> str:
    balance = await balance_service.get_balance(session, user_id)
    orders = await session.scalar(
        select(func.count()).select_from(Order).where(Order.user_id == user_id)
    )
    return texts.PROFILE.format(
        user_id=user_id,
        balance=texts.money(balance, config.currency),
        orders=int(orders or 0),
    )


async def _show_profile(event, db: Database, config: BotConfig, user_id: int) -> None:
    async with db.session() as session:
        caption = await _profile_caption(session, config, user_id)
    await render(event, banner="profile", caption=caption, reply_markup=kb.profile_kb())


@router.message(F.text == "🟢 Мой профиль")
async def msg_profile(message: Message, db: Database, config: BotConfig) -> None:
    async with db.session() as session:
        await order_service.ensure_user(
            session, message.from_user.id, message.from_user.username, message.from_user.full_name
        )
        await session.commit()
    await _show_profile(message, db, config, message.from_user.id)


@router.callback_query(F.data == "profile")
async def cb_profile(
    call: CallbackQuery, db: Database, config: BotConfig, state: FSMContext
) -> None:
    await state.clear()
    await _show_profile(call, db, config, call.from_user.id)
    await call.answer()


@router.callback_query(F.data == "balance")
async def cb_balance(
    call: CallbackQuery, db: Database, config: BotConfig, state: FSMContext
) -> None:
    await state.clear()
    async with db.session() as session:
        balance = await balance_service.get_balance(session, call.from_user.id)
    await render(
        call,
        banner="profile",
        caption=texts.BALANCE_VIEW.format(balance=texts.money(balance, config.currency)),
        reply_markup=kb.balance_kb(),
    )
    await call.answer()


# ── Пополнение ─────────────────────────────────────────────────────────────
@router.callback_query(F.data == "topup")
async def cb_topup(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TopUpFlow.waiting_amount)
    await render(
        call,
        banner="profile",
        caption=texts.TOPUP_ASK_AMOUNT.format(min=TOPUP_MIN, max=TOPUP_MAX),
        reply_markup=kb.topup_cancel_kb(),
    )
    await call.answer()


@router.message(TopUpFlow.waiting_amount)
async def msg_topup_amount(
    message: Message, config: BotConfig, state: FSMContext
) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        await message.answer(texts.TOPUP_BAD_AMOUNT.format(min=TOPUP_MIN, max=TOPUP_MAX))
        return
    if amount < TOPUP_MIN or amount > TOPUP_MAX:
        await message.answer(texts.TOPUP_BAD_AMOUNT.format(min=TOPUP_MIN, max=TOPUP_MAX))
        return

    await state.update_data(amount=str(amount))
    await state.set_state(TopUpFlow.waiting_network)
    await render(
        message,
        banner="profile",
        caption=texts.TOPUP_CHOOSE_NETWORK.format(amount=texts.money(amount, config.currency)),
        reply_markup=kb.topup_networks_kb(config.networks),
    )


@router.callback_query(TopUpFlow.waiting_network, F.data.startswith("tunet:"))
async def cb_topup_network(
    call: CallbackQuery,
    db: Database,
    config: BotConfig,
    provider: PaymentProvider,
    state: FSMContext,
) -> None:
    net = call.data.split(":", 1)[1].upper()
    if net not in config.networks:
        await call.answer("Сеть недоступна", show_alert=True)
        return
    data = await state.get_data()
    amount = Decimal(data.get("amount", "0"))
    await state.clear()
    if amount < TOPUP_MIN:
        await call.answer("Сумма не задана, начните заново", show_alert=True)
        return

    async with db.session() as session:
        await order_service.ensure_user(
            session, call.from_user.id, call.from_user.username, call.from_user.full_name
        )
        topup = await balance_service.create_topup(
            session, user_id=call.from_user.id, amount_usd=amount
        )
        try:
            invoice = await provider.create_invoice(
                amount=amount,
                client_ref=topup.client_ref,
                description=f"Balance top-up {call.from_user.id}",
                notify_url=config.notify_url,
                success_url=config.public_base_url,
                network=net,
            )
        except Exception:  # noqa: BLE001
            await session.rollback()
            await call.answer(
                "Не удалось создать счёт. Попробуйте другую сеть или позже.",
                show_alert=True,
            )
            raise

        topup.provider = provider.name
        topup.provider_order_id = invoice.provider_order_id
        topup.checkout_url = invoice.checkout_url
        topup.address = invoice.address
        topup.network = invoice.network
        topup.pay_amount = invoice.amount
        topup.currency = invoice.currency
        topup.expires_at = invoice.expires_at
        await session.commit()
        topup_id = topup.id

    await render(
        call,
        banner="profile",
        caption=texts.TOPUP_CREATED.format(
            credit=texts.money(amount, config.currency),
            amount=texts.money(invoice.amount, invoice.currency),
            network=invoice.network,
            address=invoice.address,
        ),
        reply_markup=kb.topup_payment_kb(topup_id, invoice.checkout_url, invoice.address),
    )
    await call.answer()


@router.callback_query(F.data.startswith("tucheck:"))
async def cb_topup_check(
    call: CallbackQuery, db: Database, config: BotConfig, provider: PaymentProvider
) -> None:
    topup_id = int(call.data.split(":", 1)[1])
    async with db.session() as session:
        topup = await session.get(TopUp, topup_id)
        if not topup or topup.user_id != call.from_user.id:
            await call.answer("Пополнение не найдено", show_alert=True)
            return

        if topup.status == TopUpStatus.PAID:
            balance = await balance_service.get_balance(session, call.from_user.id)
            await render(
                call, banner="profile",
                caption=texts.TOPUP_SUCCESS.format(
                    credit=texts.money(topup.amount_usd, config.currency),
                    balance=texts.money(balance, config.currency),
                ),
                reply_markup=kb.back_profile_kb(),
            )
            await call.answer()
            return

        if (
            topup.expires_at
            and datetime.utcnow() > topup.expires_at
            and topup.status == TopUpStatus.PENDING
        ):
            topup.status = TopUpStatus.EXPIRED
            await session.commit()
            await render(
                call, banner="profile", caption=texts.TOPUP_EXPIRED,
                reply_markup=kb.back_profile_kb(),
            )
            await call.answer()
            return

        update = await provider.get_status(topup.client_ref)
        if update.status != "paid":
            await call.answer(texts.TOPUP_PENDING, show_alert=True)
            return

        topup.status = TopUpStatus.PAID
        topup.tx_hash = update.tx_hash
        topup.paid_at = datetime.utcnow()
        await balance_service.credit(session, call.from_user.id, topup.amount_usd)
        await session.commit()
        balance = await balance_service.get_balance(session, call.from_user.id)

    await render(
        call,
        banner="profile",
        caption=texts.TOPUP_SUCCESS.format(
            credit=texts.money(topup.amount_usd, config.currency),
            balance=texts.money(balance, config.currency),
        ),
        reply_markup=kb.back_profile_kb(),
    )
    await call.answer("Баланс пополнен ✅")


# ── Списки ─────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "myorders")
async def cb_my_orders(call: CallbackQuery, db: Database, config: BotConfig) -> None:
    async with db.session() as session:
        rows = await session.execute(
            select(Order, Variant.title)
            .join(Variant, Variant.id == Order.variant_id)
            .where(Order.user_id == call.from_user.id)
            .order_by(Order.id.desc())
            .limit(15)
        )
        items = rows.all()

    if not items:
        caption = texts.MY_ORDERS_TITLE + "\n\n" + texts.NO_ORDERS
    else:
        lines = [texts.MY_ORDERS_TITLE, ""]
        for order, vtitle in items:
            total = Decimal(order.price_usd) * (order.quantity or 1)
            line = (
                f"#{order.id} · {vtitle} ×{order.quantity or 1} · "
                f"{texts.money(total, config.currency)} · {_status_ru(order.status)}"
            )
            if order.status == OrderStatus.COMPLETED and order.delivery_code:
                codes = ", ".join(order.delivery_code.splitlines())
                line += f"\n   коды: <code>{codes}</code>"
            lines.append(line)
        caption = "\n".join(lines)

    await render(call, banner="profile", caption=caption, reply_markup=kb.back_profile_kb())
    await call.answer()


@router.callback_query(F.data == "mytopups")
async def cb_my_topups(call: CallbackQuery, db: Database, config: BotConfig) -> None:
    async with db.session() as session:
        topups = await balance_service.list_topups(session, call.from_user.id)

    if not topups:
        caption = texts.MY_TOPUPS_TITLE + "\n\n" + texts.NO_TOPUPS
    else:
        lines = [texts.MY_TOPUPS_TITLE, ""]
        for t in topups:
            lines.append(
                f"#{t.id} · {texts.money(t.amount_usd, config.currency)} · "
                f"{(t.network or '-')} · {_status_ru(t.status)}"
            )
        caption = "\n".join(lines)

    await render(call, banner="profile", caption=caption, reply_markup=kb.back_profile_kb())
    await call.answer()
