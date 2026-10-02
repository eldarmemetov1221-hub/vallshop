"""Запросы каталога: активные товары и их вариации."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db.models import Product, Variant


async def list_products(
    session: AsyncSession, *, only_active: bool = True
) -> List[Product]:
    stmt = select(Product).order_by(Product.sort_order, Product.id)
    if only_active:
        stmt = stmt.where(Product.is_active.is_(True))
    return list(await session.scalars(stmt))


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
    return await session.get(Variant, variant_id)
