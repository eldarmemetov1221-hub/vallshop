"""Подкатегории товаров (self-referential parent_id)."""

import pytest

from bot.db import Database, Product
from bot.services import catalog as catalog_service


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    yield database
    await database.dispose()


@pytest.mark.asyncio
async def test_top_level_excludes_children_and_lists_them(db):
    async with db.session() as s:
        parent = Product(game="PUBG", title="PUBG UC")
        s.add(parent)
        await s.flush()
        s.add(Product(game="PUBG", title="PUBG UC Global", parent_id=parent.id))
        s.add(Product(game="PUBG", title="PUBG UC RU", parent_id=parent.id))
        s.add(Product(game="Roblox", title="Roblox"))  # другой верхний уровень
        await s.commit()
        pid = parent.id

    async with db.session() as s:
        top = await catalog_service.list_products(s)
        titles = {p.title for p in top}
        assert titles == {"PUBG UC", "Roblox"}  # дети не на верхнем уровне

        children = await catalog_service.list_children(s, pid)
        assert {c.title for c in children} == {"PUBG UC Global", "PUBG UC RU"}
        assert await catalog_service.children_count(s, pid) == 2
        assert all(c.parent_id == pid for c in children)
