"""Статистика магазина: пользователи, продажи, выручка, прибыль.

Продажей считаем завершённый заказ (OrderStatus.COMPLETED). Прибыль =
выручка (price_usd * qty) − себестоимость (variant.cost_usd * qty).
Все суммы в USDT. Время — UTC (как хранится created_at).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Order, OrderStatus, User, Variant
from . import rates as rates_service


def day_bounds(d: datetime) -> Tuple[datetime, datetime]:
    start = datetime(d.year, d.month, d.day)
    return start, start + timedelta(days=1)


def preset_range(preset: str) -> Tuple[Optional[datetime], datetime, str]:
    """Вернуть (start|None, end, подпись) для пресета."""
    now = datetime.utcnow()
    if preset == "today":
        start, _ = day_bounds(now)
        return start, now, "Сегодня"
    if preset == "7d":
        return now - timedelta(days=7), now, "7 дней"
    if preset == "30d":
        return now - timedelta(days=30), now, "30 дней"
    if preset == "month":
        start = datetime(now.year, now.month, 1)
        return start, now, "Этот месяц"
    # all
    return None, now, "Всё время"


async def users_stats(
    session: AsyncSession, start: Optional[datetime], end: datetime
) -> dict:
    total = await session.scalar(select(func.count()).select_from(User)) or 0
    stmt = select(func.count()).select_from(User).where(User.created_at < end)
    if start is not None:
        stmt = stmt.where(User.created_at >= start)
    new = await session.scalar(stmt) or 0
    return {"total": int(total), "new": int(new)}


async def total_balance(session: AsyncSession) -> Decimal:
    """Суммарный баланс всех клиентов (₽, текущий снимок — не зависит от периода)."""
    total = await session.scalar(
        select(func.coalesce(func.sum(User.balance), 0))
    )
    return Decimal(total or 0)


async def sales_stats(
    session: AsyncSession, start: Optional[datetime], end: datetime
) -> dict:
    conds = [Order.status == OrderStatus.COMPLETED, Order.created_at < end]
    if start is not None:
        conds.append(Order.created_at >= start)
    row = (
        await session.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.quantity), 0),
                func.coalesce(func.sum(Order.price_usd * Order.quantity), 0),
                func.coalesce(
                    func.sum(
                        func.coalesce(Order.cost_usd, Variant.cost_usd * Order.quantity)
                    ),
                    0,
                ),
            )
            .select_from(Order)
            .join(Variant, Variant.id == Order.variant_id)
            .where(*conds)
        )
    ).one()
    orders, items, revenue, cost_usd = row
    # Выручка (списанное с клиентов) уже в рублях. Себестоимость — в USDT,
    # переводим в рубли по текущему курсу для расчёта прибыли.
    revenue = Decimal(revenue or 0)
    cost_usd = Decimal(cost_usd or 0)
    rate = rates_service.get_rate()
    cost_rub = (cost_usd * rate)
    return {
        "orders": int(orders or 0),
        "items": int(items or 0),
        "revenue": revenue,          # ₽
        "cost": cost_rub,            # ₽ (себестоимость по курсу)
        "cost_usd": cost_usd,        # USDT (справочно)
        "profit": revenue - cost_rub,  # ₽
    }
