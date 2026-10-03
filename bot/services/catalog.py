"""Запросы каталога: активные товары и их вариации."""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db.models import Product, Variant

log = logging.getLogger("vallshop.catalog")


def _as_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


async def fazercard_stock(fzr, variants: List[Variant]) -> Dict[int, Optional[int]]:
    """Живой сток FazerCard для номиналов source='fazercard'.

    Возвращает ``{variant_id: stock}``; ``None`` — если сток неизвестен
    (ошибка запроса / поле отсутствует). Группирует по категории, чтобы
    сделать один запрос на (kind, category_id). Для не-FazerCard номиналов
    ключей не добавляет.
    """
    result: Dict[int, Optional[int]] = {}
    groups: Dict[tuple, List[Variant]] = {}
    for v in variants:
        if getattr(v, "source", "stock") == "fazercard" and v.fzr_a and v.fzr_b:
            groups.setdefault((v.fzr_kind, v.fzr_a), []).append(v)
    if not groups or fzr is None:
        return result

    for (kind, cat_id), vs in groups.items():
        try:
            offers = await asyncio.to_thread(fzr.offers_for, kind, cat_id)
            smap = {str(o.get("id")): _as_int(o.get("stock")) for o in offers}
        except Exception:  # noqa: BLE001 — витрина не должна падать из-за поставщика
            log.warning("Не удалось получить сток FazerCard для %s/%s", kind, cat_id)
            smap = {}
            for v in vs:
                result[v.id] = None
            continue
        for v in vs:
            result[v.id] = smap.get(str(v.fzr_b))
    return result


async def list_products(
    session: AsyncSession,
    *,
    only_active: bool = True,
    parent_id: Optional[int] = None,
) -> List[Product]:
    """Товары уровня ``parent_id`` (по умолчанию — верхний уровень, parent IS NULL)."""
    stmt = select(Product).order_by(Product.sort_order, Product.id)
    if parent_id is None:
        stmt = stmt.where(Product.parent_id.is_(None))
    else:
        stmt = stmt.where(Product.parent_id == parent_id)
    if only_active:
        stmt = stmt.where(Product.is_active.is_(True))
    return list(await session.scalars(stmt))


async def list_children(
    session: AsyncSession, parent_id: int, *, only_active: bool = True
) -> List[Product]:
    return await list_products(session, only_active=only_active, parent_id=parent_id)


async def children_count(session: AsyncSession, parent_id: int) -> int:
    from sqlalchemy import func
    return int(
        await session.scalar(
            select(func.count()).select_from(Product).where(Product.parent_id == parent_id)
        )
        or 0
    )


async def get_product(
    session: AsyncSession, product_id: int, *, with_variants: bool = True
) -> Optional[Product]:
    stmt = select(Product).where(Product.id == product_id)
    if with_variants:
        stmt = stmt.options(selectinload(Product.variants))
    return await session.scalar(stmt)


async def list_variants(
    session: AsyncSession, product_id: int, *, only_active: bool = True
) -> List[Variant]:
    stmt = (
        select(Variant)
        .where(Variant.product_id == product_id)
        .order_by(Variant.sort_order, Variant.id)
    )
    if only_active:
        stmt = stmt.where(Variant.is_active.is_(True))
    return list(await session.scalars(stmt))


async def get_variant(session: AsyncSession, variant_id: int) -> Optional[Variant]:
    # Жадно подгружаем product, чтобы обращение к variant.product не вызывало
    # ленивую загрузку в async-контексте (SQLAlchemy async её не допускает).
    return await session.scalar(
        select(Variant)
        .where(Variant.id == variant_id)
        .options(selectinload(Variant.product))
    )
