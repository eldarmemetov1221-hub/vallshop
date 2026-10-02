"""Фоновый поллер: дотягивает топап-заказы в статусе FULFILLING до финала.

Нужен, когда поставщик LioGames выдаёт код не мгновенно (status=PROCESSING).
Опрашивает order-status с учётом интервала и уведомляет покупателя.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from sqlalchemy import select

from liogames import LioGamesClient

from ..db import Database
from ..db.models import Order, OrderStatus
from ..services import catalog as catalog_service
from ..services import orders as order_service
from .. import texts

log = logging.getLogger("vallshop.poller")


async def run_fulfillment_poller(
    bot: Bot, db: Database, liog: LioGamesClient, interval: float = 20.0
) -> None:
    """Бесконечный цикл опроса незавершённых топап-заказов."""
    while True:
        try:
            await _tick(bot, db, liog)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — поллер не должен падать
            log.exception("Ошибка в цикле поллера выдачи")
        await asyncio.sleep(interval)


async def _tick(bot: Bot, db: Database, liog: LioGamesClient) -> None:
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
            status = await order_service.poll_topup(session, fresh, liog)
            user_id = fresh.user_id
            code = fresh.delivery_code
            ref = fresh.client_ref
            await session.commit()

        if status == OrderStatus.COMPLETED and code:
            await _notify(bot, user_id, texts.DELIVERY_SUCCESS.format(code=code))
        elif status == OrderStatus.FAILED:
            await _notify(bot, user_id, texts.FULFILL_FAILED.format(ref=ref))


async def _notify(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text)
    except Exception:  # noqa: BLE001
        log.warning("Не удалось отправить сообщение user=%s", user_id)
