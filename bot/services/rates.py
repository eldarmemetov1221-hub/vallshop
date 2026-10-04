"""Курс USDT→₽ с синхронным кэшем (цены считаются в sync-коде клавиатур).

Кэш грузится при старте и обновляется при изменении курса админом.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from ..db import Database
from ..services import settings as settings_service

log = logging.getLogger("vallshop.rates")

DEFAULT_RATE = Decimal("95")
_rate: Decimal = DEFAULT_RATE


def get_rate() -> Decimal:
    return _rate


async def load(db: Database) -> None:
    global _rate
    try:
        async with db.session() as session:
            raw = await settings_service.get(session, settings_service.USDT_RATE)
        if raw:
            _rate = Decimal(raw)
    except Exception:  # noqa: BLE001 — кэш курса не должен ронять бота
        log.exception("Не удалось загрузить курс USDT→₽")


async def set_rate(db: Database, value: Decimal) -> None:
    global _rate
    async with db.session() as session:
        await settings_service.set(session, settings_service.USDT_RATE, str(value))
        await session.commit()
    _rate = Decimal(value)
