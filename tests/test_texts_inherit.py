"""Наследование редактируемых текстов «ожидание»/«выдача».

resolve_text: номинал → товар → родительская категория → дефолт.
None = наследовать дальше, "" = скрыть (остановить наследование).
"""

import pytest
from decimal import Decimal

from bot.db import Database, Product, Variant
from bot.services import catalog as catalog_service

DEFAULT = "DEFAULT"


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    yield database
    await database.dispose()


async def _tree(db, *, parent_text=None, product_text=None, variant_text=None):
    """Создаёт parent → product → variant с заданными pending_text и возвращает vid."""
    async with db.session() as s:
        parent = Product(game="PUBG", title="PUBG UC", pending_text=parent_text)
        s.add(parent)
        await s.flush()
        product = Product(
            game="PUBG", title="PUBG UC Global",
            parent_id=parent.id, pending_text=product_text,
        )
        s.add(product)
        await s.flush()
        v = Variant(
            product_id=product.id, title="325 UC",
            liog_product_id=0, liog_variation_id=0,
            cost_usd=Decimal("1"), pending_text=variant_text,
        )
        s.add(v)
        await s.commit()
        return v.id


@pytest.mark.asyncio
async def test_falls_back_to_default(db):
    vid = await _tree(db)  # всё None
    async with db.session() as s:
        v = await s.get(Variant, vid)
        assert await catalog_service.resolve_text(s, v, "pending_text", DEFAULT) == DEFAULT


@pytest.mark.asyncio
async def test_inherits_from_parent(db):
    vid = await _tree(db, parent_text="от категории")
    async with db.session() as s:
        v = await s.get(Variant, vid)
        assert await catalog_service.resolve_text(s, v, "pending_text", DEFAULT) == "от категории"


@pytest.mark.asyncio
async def test_product_overrides_parent(db):
    vid = await _tree(db, parent_text="от категории", product_text="от товара")
    async with db.session() as s:
        v = await s.get(Variant, vid)
        assert await catalog_service.resolve_text(s, v, "pending_text", DEFAULT) == "от товара"


@pytest.mark.asyncio
async def test_variant_wins(db):
    vid = await _tree(db, parent_text="кат", product_text="тов", variant_text="номинал")
    async with db.session() as s:
        v = await s.get(Variant, vid)
        assert await catalog_service.resolve_text(s, v, "pending_text", DEFAULT) == "номинал"


@pytest.mark.asyncio
async def test_empty_string_hides_and_stops_inheritance(db):
    # у товара явно "" (скрыть) — не должно наследоваться от родителя
    vid = await _tree(db, parent_text="от категории", product_text="")
    async with db.session() as s:
        v = await s.get(Variant, vid)
        assert await catalog_service.resolve_text(s, v, "pending_text", DEFAULT) == ""


@pytest.mark.asyncio
async def test_variant_empty_hides_over_product(db):
    vid = await _tree(db, product_text="от товара", variant_text="")
    async with db.session() as s:
        v = await s.get(Variant, vid)
        assert await catalog_service.resolve_text(s, v, "pending_text", DEFAULT) == ""
