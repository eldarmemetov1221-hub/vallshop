"""Разовая раскладка кнопок каталога (_reorder_catalog_v1)."""

import pytest

from bot.db import Database, Product
from bot.main import _reorder_catalog_v1
from bot.services import catalog as catalog_service


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    yield database
    await database.dispose()


@pytest.mark.asyncio
async def test_reorder_sets_order_and_width(db):
    async with db.session() as s:
        # создаём в «неправильном» порядке с дефолтными флагами
        s.add_all([
            Product(game="Игры", title="Игры и Сервисы"),
            Product(game="tg", title="Телеграм Премиум"),
            Product(game="STEAM_TOPUP", title="Пополнить Steam", full_width=True, sort_order=40),
            Product(game="TG_STARS", title="Телеграм Звёзды", full_width=True, sort_order=10),
            Product(game="VPN", title="VPN | VallShop", full_width=True, sort_order=50),
        ])
        await s.commit()

    await _reorder_catalog_v1(db)

    async with db.session() as s:
        products = await catalog_service.list_products(s, only_active=False)
        order = [(p.title, p.full_width) for p in products]

    assert order == [
        ("Телеграм Звёзды", False),
        ("Телеграм Премиум", False),
        ("Игры и Сервисы", True),
        ("Пополнить Steam", True),
        ("VPN | VallShop", True),
    ]


@pytest.mark.asyncio
async def test_reorder_runs_once(db):
    async with db.session() as s:
        s.add(Product(game="TG_STARS", title="Телеграм Звёзды", full_width=True, sort_order=10))
        await s.commit()
    await _reorder_catalog_v1(db)
    # после миграции админ мог бы что-то поменять — повторный прогон не трогает
    async with db.session() as s:
        p = (await catalog_service.list_products(s, only_active=False))[0]
        p.full_width = True
        await s.commit()
    await _reorder_catalog_v1(db)
    async with db.session() as s:
        p = (await catalog_service.list_products(s, only_active=False))[0]
        assert p.full_width is True  # не перезаписано
