"""Удаление категорий/товаров и номиналов (с защитой по заказам)."""

import pytest
from decimal import Decimal

from bot.db import Database
from bot.db.models import Order, Product, StockItem, StockStatus, User, Variant
from bot.services import catalog as catalog_service


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    yield database
    await database.dispose()


async def _variant(s, product_id, **kw):
    v = Variant(
        product_id=product_id, title=kw.pop("title", "60 UC"),
        liog_product_id=0, liog_variation_id=kw.pop("vid", 0),
        cost_usd=Decimal("1"), **kw,
    )
    s.add(v)
    await s.flush()
    return v


@pytest.mark.asyncio
async def test_delete_variant_removes_stock(db):
    async with db.session() as s:
        p = Product(game="PUBG", title="PUBG UC")
        s.add(p)
        await s.flush()
        v = await _variant(s, p.id)
        s.add(StockItem(variant_id=v.id, code="ABC", status=StockStatus.AVAILABLE))
        await s.commit()
        vid = v.id

    async with db.session() as s:
        ok, reason = await catalog_service.delete_variant(s, vid)
        assert ok and reason == "ok"
    async with db.session() as s:
        assert await s.get(Variant, vid) is None
        from sqlalchemy import func, select
        left = await s.scalar(
            select(func.count()).select_from(StockItem).where(StockItem.variant_id == vid)
        )
        assert left == 0


@pytest.mark.asyncio
async def test_delete_variant_blocked_by_order(db):
    async with db.session() as s:
        s.add(User(id=1, username="buyer"))
        p = Product(game="PUBG", title="PUBG UC")
        s.add(p)
        await s.flush()
        v = await _variant(s, p.id)
        s.add(Order(client_ref="r1", user_id=1, variant_id=v.id, price_usd=Decimal("5")))
        await s.commit()
        vid = v.id

    async with db.session() as s:
        ok, reason = await catalog_service.delete_variant(s, vid)
        assert not ok and reason == "orders"
    async with db.session() as s:
        assert await s.get(Variant, vid) is not None  # остался


@pytest.mark.asyncio
async def test_delete_product_cascades_children(db):
    async with db.session() as s:
        parent = Product(game="PUBG", title="PUBG UC")
        s.add(parent)
        await s.flush()
        child = Product(game="PUBG", title="Global", parent_id=parent.id)
        s.add(child)
        await s.flush()
        await _variant(s, child.id, vid=1)
        await _variant(s, parent.id, vid=2)
        await s.commit()
        pid = parent.id

    async with db.session() as s:
        ok, reason = await catalog_service.delete_product(s, pid)
        assert ok and reason == "ok"
    async with db.session() as s:
        from sqlalchemy import func, select
        prods = await s.scalar(select(func.count()).select_from(Product))
        vars_ = await s.scalar(select(func.count()).select_from(Variant))
        assert prods == 0 and vars_ == 0


@pytest.mark.asyncio
async def test_delete_product_blocked_by_nested_order(db):
    async with db.session() as s:
        s.add(User(id=1, username="buyer"))
        parent = Product(game="PUBG", title="PUBG UC")
        s.add(parent)
        await s.flush()
        child = Product(game="PUBG", title="Global", parent_id=parent.id)
        s.add(child)
        await s.flush()
        v = await _variant(s, child.id, vid=1)
        s.add(Order(client_ref="r1", user_id=1, variant_id=v.id, price_usd=Decimal("5")))
        await s.commit()
        pid = parent.id

    async with db.session() as s:
        ok, reason = await catalog_service.delete_product(s, pid)
        assert not ok and reason == "orders"
    async with db.session() as s:
        from sqlalchemy import func, select
        assert await s.scalar(select(func.count()).select_from(Product)) == 2


@pytest.mark.asyncio
async def test_reparent_moves_into_category(db):
    async with db.session() as s:
        hub = Product(game="Игры", title="Игры и Сервисы")
        pubg = Product(game="PUBG", title="PUBG Mobile Code")
        s.add_all([hub, pubg])
        await s.commit()
        hub_id, pubg_id = hub.id, pubg.id

    async with db.session() as s:
        ok, reason = await catalog_service.reparent_product(s, pubg_id, hub_id)
        assert ok and reason == "ok"
    async with db.session() as s:
        # теперь верхний уровень содержит только hub
        top = await catalog_service.list_products(s, only_active=False)
        assert {p.title for p in top} == {"Игры и Сервисы"}
        kids = await catalog_service.list_children(s, hub_id, only_active=False)
        assert {p.title for p in kids} == {"PUBG Mobile Code"}

    # обратно на верхний уровень
    async with db.session() as s:
        ok, _ = await catalog_service.reparent_product(s, pubg_id, None)
        assert ok
    async with db.session() as s:
        top = await catalog_service.list_products(s, only_active=False)
        assert {p.title for p in top} == {"Игры и Сервисы", "PUBG Mobile Code"}


@pytest.mark.asyncio
async def test_reparent_rejects_cycle(db):
    async with db.session() as s:
        parent = Product(game="x", title="Родитель")
        s.add(parent)
        await s.flush()
        child = Product(game="x", title="Ребёнок", parent_id=parent.id)
        s.add(child)
        await s.commit()
        parent_id, child_id = parent.id, child.id

    async with db.session() as s:
        # нельзя переместить родителя внутрь его же ребёнка
        ok, reason = await catalog_service.reparent_product(s, parent_id, child_id)
        assert not ok and reason == "cycle"
        # и в самого себя
        ok, reason = await catalog_service.reparent_product(s, parent_id, parent_id)
        assert not ok and reason == "self"
