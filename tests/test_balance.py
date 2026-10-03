"""Тесты баланса и покупки с баланса (in-memory SQLite)."""

from decimal import Decimal

import pytest

from bot.db import Database, Product, Variant
from bot.db.models import OrderStatus
from bot.services import balance as balance_service
from bot.services import orders as order_service
from bot.services import stock as stock_service


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    yield database
    await database.dispose()


async def _setup(db, codes, balance="0"):
    async with db.session() as s:
        p = Product(game="PUBG", title="T")
        s.add(p)
        await s.flush()
        v = Variant(
            product_id=p.id, title="60 UC",
            liog_product_id=1, liog_variation_id=2, cost_usd=Decimal("1.00"),
        )
        s.add(v)
        await s.flush()
        if codes:
            await stock_service.add_codes(s, v.id, codes)
        await order_service.ensure_user(s, 7, "u", "U")
        if Decimal(balance) > 0:
            await balance_service.credit(s, 7, Decimal(balance))
        await s.commit()
        return v.id


@pytest.mark.asyncio
async def test_credit_and_debit(db):
    await _setup(db, [])
    async with db.session() as s:
        await balance_service.credit(s, 7, Decimal("10"))
        await s.commit()
        assert await balance_service.get_balance(s, 7) == Decimal("10.00")
        assert await balance_service.try_debit(s, 7, Decimal("3")) is True
        assert await balance_service.try_debit(s, 7, Decimal("100")) is False
        await s.commit()
        assert await balance_service.get_balance(s, 7) == Decimal("7.00")


@pytest.mark.asyncio
async def test_purchase_deducts_and_delivers(db):
    vid = await _setup(db, ["A", "B", "C"], balance="10")
    async with db.session() as s:
        v = await s.get(Variant, vid)
        order, codes = await order_service.purchase_from_balance(
            s, user_id=7, variant=v, unit_price=Decimal("2"), quantity=2
        )
        await s.commit()
        assert codes == ["A", "B"]
        assert order.quantity == 2
        assert order.status == OrderStatus.COMPLETED
        assert await balance_service.get_balance(s, 7) == Decimal("6.00")
        assert await stock_service.available_count(s, vid) == 1


@pytest.mark.asyncio
async def test_purchase_insufficient_balance_rolls_back(db):
    vid = await _setup(db, ["A", "B"], balance="1")
    async with db.session() as s:
        v = await s.get(Variant, vid)
        with pytest.raises(order_service.InsufficientBalance):
            await order_service.purchase_from_balance(
                s, user_id=7, variant=v, unit_price=Decimal("2"), quantity=1
            )
        await s.rollback()
        assert await balance_service.get_balance(s, 7) == Decimal("1.00")
        assert await stock_service.available_count(s, vid) == 2


@pytest.mark.asyncio
async def test_purchase_out_of_stock_rolls_back(db):
    vid = await _setup(db, ["A"], balance="100")
    async with db.session() as s:
        v = await s.get(Variant, vid)
        with pytest.raises(order_service.OutOfStock):
            await order_service.purchase_from_balance(
                s, user_id=7, variant=v, unit_price=Decimal("2"), quantity=2
            )
        await s.rollback()
        assert await balance_service.get_balance(s, 7) == Decimal("100.00")
        assert await stock_service.available_count(s, vid) == 1
