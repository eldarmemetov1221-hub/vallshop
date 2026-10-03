"""Управление стоком ваучер-кодов."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import StockItem, StockStatus


async def available_count(session: AsyncSession, variant_id: int) -> int:
    result = await session.scalar(
        select(func.count())
        .select_from(StockItem)
        .where(
            StockItem.variant_id == variant_id,
            StockItem.status == StockStatus.AVAILABLE,
        )
    )
    return int(result or 0)


async def counts_by_variant(session: AsyncSession, variant_ids: Iterable[int]) -> dict[int, int]:
    """Вернуть {variant_id: доступно} для списка вариаций."""
    ids = list(variant_ids)
    if not ids:
        return {}
    rows = await session.execute(
        select(StockItem.variant_id, func.count())
        .where(
            StockItem.variant_id.in_(ids),
            StockItem.status == StockStatus.AVAILABLE,
        )
        .group_by(StockItem.variant_id)
    )
    return {vid: int(cnt) for vid, cnt in rows.all()}


async def add_codes(
    session: AsyncSession, variant_id: int, codes: Iterable[str]
) -> Tuple[int, int]:
    """Добавить коды в сток. Возвращает (добавлено, пропущено_дубликатов)."""
    existing = set(
        await session.scalars(
            select(StockItem.code).where(StockItem.variant_id == variant_id)
        )
    )
    added = 0
    skipped = 0
    seen: set[str] = set()
    for raw in codes:
        code = raw.strip()
        if not code:
            continue
        if code in existing or code in seen:
            skipped += 1
            continue
        seen.add(code)
        session.add(StockItem(variant_id=variant_id, code=code))
        added += 1
    await session.flush()
    return added, skipped


async def reserve_one(
    session: AsyncSession, variant_id: int, order_id: int
) -> Optional[StockItem]:
    """Зарезервировать один свободный код под заказ (атомарно).

    Использует условный UPDATE, чтобы два параллельных заказа не забрали
    один и тот же код.
    """
    item_id = await session.scalar(
        select(StockItem.id)
        .where(
            StockItem.variant_id == variant_id,
            StockItem.status == StockStatus.AVAILABLE,
        )
        .order_by(StockItem.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if item_id is None:
        return None

    affected = await session.execute(
        update(StockItem)
        .where(StockItem.id == item_id, StockItem.status == StockStatus.AVAILABLE)
        .values(status=StockStatus.RESERVED, order_id=order_id)
    )
    if affected.rowcount != 1:
        return None
    return await session.get(StockItem, item_id)


async def reserve_many(
    session: AsyncSession, variant_id: int, order_id: int, qty: int
) -> List[StockItem]:
    """Зарезервировать qty свободных кодов под заказ.

    Возвращает список зарезервированных позиций (ровно qty), либо пустой список,
    если не удалось набрать нужное количество (тогда ничего не резервируется —
    вызывающий код должен откатить транзакцию).
    """
    reserved: List[StockItem] = []
    for _ in range(qty):
        item = await reserve_one(session, variant_id, order_id)
        if item is None:
            return []  # не хватило — откат делает вызывающий
        reserved.append(item)
    return reserved


async def mark_sold(session: AsyncSession, item: StockItem) -> None:
    item.status = StockStatus.SOLD
    item.sold_at = datetime.utcnow()
    await session.flush()


async def release(session: AsyncSession, item: StockItem) -> None:
    """Вернуть зарезервированный код обратно в сток (например, при ошибке)."""
    item.status = StockStatus.AVAILABLE
    item.order_id = None
    await session.flush()
