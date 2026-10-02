"""Async-тесты БД/сервисов на in-memory SQLite: сток и выдача из стока."""

from decimal import Decimal

import pytest

from bot.db import Database
from bot.db.models import OrderStatus, Product, StockStatus, Variant
from bot.services import orders as order_service
from bot.services import stock as stock_service


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    yield database
    await database.dispose()


async def _make_variant(db) -> int:
    async with db.session() as session:
        product = Product(game="PUBG", title="PUBG Mobile Code (Global)")
        session.add(product)
        await session.flush()
        variant = Variant(
            product_id=product.id,
            title="60 UC",
            liog_product_id=66599,
            liog_variation_id=534124,
            cost_usd=Decimal("0.88"),
        )
        session.add(variant)
        await session.commit()
        return variant.id


@pytest.mark.asyncio
async def test_add_stock_and_count(db):
    vid = await _make_variant(db)
    async with db.session() as session:
        added, skipped = await stock_service.add_codes(
            session, vid, ["AAA", "BBB", "AAA", ""]
        )
        await session.commit()
        assert added == 2
        assert skipped == 1
        assert await stock_service.available_count(session, vid) == 2


@pytest.mark.asyncio
async def test_fulfill_from_stock_delivers_code(db):
    vid = await _make_variant(db)
    async with db.session() as session:
        await stock_service.add_codes(session, vid, ["CODE-123"])
        await session.commit()

    async with db.session() as session:
        variant = await session.get(Variant, vid)
        await order_service.ensure_user(session, 42, "tester", "Tester")
        order = await order_service.create_order(
            session, user_id=42, variant=variant, price_usd=Decimal("1.00")
        )
        code = await order_service.fulfill_from_stock(session, order)
        await session.commit()

        assert code == "CODE-123"
        assert order.status == OrderStatus.COMPLETED
        assert order.delivery_code == "CODE-123"
        assert await stock_service.available_count(session, vid) == 0


@pytest.mark.asyncio
async def test_fulfill_from_empty_stock_returns_none(db):
    vid = await _make_variant(db)
    async with db.session() as session:
        variant = await session.get(Variant, vid)
        await order_service.ensure_user(session, 7, None, None)
        order = await order_service.create_order(
            session, user_id=7, variant=variant, price_usd=Decimal("1.00")
        )
        code = await order_service.fulfill_from_stock(session, order)
        await session.commit()
        assert code is None
