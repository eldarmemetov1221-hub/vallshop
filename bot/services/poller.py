"""Фоновый поллер: дотягивает топап-заказы в статусе FULFILLING до финала.

Нужен, когда поставщик LioGames выдаёт код не мгновенно (status=PROCESSING).
Опрашивает order-status с учётом интервала и уведомляет покупателя.
"""

from __future__ import annotations

import asyncio
import logging

from datetime import datetime
from decimal import Decimal

from aiogram import Bot
from sqlalchemy import select

from liogames import LioGamesClient
from fazercard import FazerCardClient

from ..db import Database
from ..db.models import Order, OrderStatus, TopUp, TopUpStatus
from ..services import balance as balance_service
from ..services import orders as order_service
from .. import texts

log = logging.getLogger("vallshop.poller")


async def run_fulfillment_poller(
    bot: Bot,
    db: Database,
    liog: LioGamesClient,
    fzr: FazerCardClient | None = None,
    interval: float = 20.0,
) -> None:
    """Бесконечный цикл опроса незавершённых заказов (LioGames + FazerCard)."""
    while True:
        try:
            await _tick(bot, db, liog, fzr)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — поллер не должен падать
            log.exception("Ошибка в цикле поллера выдачи")
        await asyncio.sleep(interval)


async def _tick(
    bot: Bot, db: Database, liog: LioGamesClient, fzr: FazerCardClient | None
) -> None:
    async with db.session() as session:
        orders = list(
            await session.scalars(
                select(Order).where(Order.status == OrderStatus.FULFILLING)
            )
        )

    for order in orders:
        async with db.session() as session:
            fresh = await session.get(Order, order.id)
            if not fresh or fresh.status != OrderStatus.FULFILLING:
                continue

            refunded = False
            if fresh.supplier == "fazercard":
                if fzr is None:
                    continue  # клиент не настроен — пропускаем
                status = await order_service.poll_fazercard(session, fresh, fzr)
                if status == OrderStatus.FAILED:
                    # buy-on-demand оплачивался с баланса — возвращаем деньги.
                    refund = Decimal(fresh.price_usd) * (fresh.quantity or 1)
                    await balance_service.credit(session, fresh.user_id, refund)
                    fresh.status = OrderStatus.REFUNDED
                    status = OrderStatus.REFUNDED
                    refunded = True
            else:
                status = await order_service.poll_topup(session, fresh, liog)

            user_id = fresh.user_id
            code = fresh.delivery_code
            ref = fresh.client_ref
            refund_amount = Decimal(fresh.price_usd) * (fresh.quantity or 1)
            await session.commit()

        if status == OrderStatus.COMPLETED and code:
            await _notify(bot, user_id, texts.DELIVERY_SUCCESS.format(code=code))
        elif status == OrderStatus.REFUNDED and refunded:
            await _notify(
                bot, user_id,
                texts.FULFILL_REFUNDED.format(ref=ref, amount=refund_amount),
            )
        elif status == OrderStatus.FAILED:
            await _notify(bot, user_id, texts.FULFILL_FAILED.format(ref=ref))


async def run_topup_poller(
    bot: Bot, db: Database, provider, interval: float = 30.0
) -> None:
    """Фоновая авто-проверка пополнений (подстраховка вебхука).

    Опрашивает провайдера по PENDING-пополнениям: при оплате — зачисляет
    баланс и уведомляет, при истечении срока — помечает EXPIRED. Операции
    идемпотентны (повтор с вебхуком безопасен)."""
    while True:
        try:
            await _topup_tick(bot, db, provider)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка в цикле поллера пополнений")
        await asyncio.sleep(interval)


async def _topup_tick(bot: Bot, db: Database, provider) -> None:
    async with db.session() as session:
        pending = list(
            await session.scalars(
                select(TopUp).where(TopUp.status == TopUpStatus.PENDING)
            )
        )

    for t in pending:
        async with db.session() as session:
            fresh = await session.get(TopUp, t.id)
            if not fresh or fresh.status != TopUpStatus.PENDING:
                continue
            if fresh.expires_at and datetime.utcnow() > fresh.expires_at:
                fresh.status = TopUpStatus.EXPIRED
                await session.commit()
                continue
            try:
                update = await provider.get_status(fresh.client_ref)
            except Exception:  # noqa: BLE001 — провайдер недоступен, попробуем позже
                continue
            if getattr(update, "status", None) != "paid":
                continue
            fresh.status = TopUpStatus.PAID
            fresh.tx_hash = getattr(update, "tx_hash", None)
            fresh.paid_at = datetime.utcnow()
            await balance_service.credit(session, fresh.user_id, fresh.amount_usd)
            await session.commit()
            user_id, credit = fresh.user_id, fresh.amount_usd
            balance = await balance_service.get_balance(session, user_id)

        await _notify(
            bot, user_id,
            texts.TOPUP_SUCCESS.format(
                credit=texts.money(credit), balance=texts.money(balance)
            ),
        )


async def _notify(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text)
    except Exception:  # noqa: BLE001
        log.warning("Не удалось отправить сообщение user=%s", user_id)
