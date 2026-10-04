"""Оформление меню: переопределения текста/эмодзи/видимости/порядка."""

import pytest

from bot.db import Database
from bot.services import menu as menu_service


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    await menu_service.load(database)  # сбросить кэш к дефолтам этого db
    yield database
    await database.dispose()


@pytest.mark.asyncio
async def test_defaults(db):
    main = menu_service.menu_entries("main")
    keys = [r.key for r in main]
    assert keys[0] == "main.catalog"
    assert all(r.enabled for r in main)


@pytest.mark.asyncio
async def test_set_label_and_reset(db):
    await menu_service.set_label(db, "main.catalog", "Магазин")
    assert menu_service.resolved("main.catalog").text == "Магазин"
    await menu_service.set_label(db, "main.catalog", None)
    assert menu_service.resolved("main.catalog").text == "Каталог"


@pytest.mark.asyncio
async def test_set_emoji_remove_and_reset(db):
    await menu_service.set_emoji(db, "main.catalog", "123456")
    assert menu_service.resolved("main.catalog").emoji_id == "123456"
    await menu_service.set_emoji(db, "main.catalog", "")  # убрать
    assert menu_service.resolved("main.catalog").emoji_id is None
    await menu_service.set_emoji(db, "main.catalog", None)  # сброс
    assert menu_service.resolved("main.catalog").emoji_id == "5805550320985578625"


@pytest.mark.asyncio
async def test_toggle_hides_from_clients(db):
    await menu_service.toggle(db, "main.reviews")
    vis_keys = [r.key for r in menu_service.menu_entries("main")]  # only_enabled=True
    assert "main.reviews" not in vis_keys
    # но в админском списке (all) — присутствует и помечен выключенным
    all_keys = [r.key for r in menu_service.menu_entries("main", only_enabled=False)]
    assert "main.reviews" in all_keys
    assert menu_service.resolved("main.reviews").enabled is False


@pytest.mark.asyncio
async def test_move_reorders(db):
    before = [r.key for r in menu_service.menu_entries("main", only_enabled=False)]
    first, second = before[0], before[1]
    await menu_service.move(db, second, -1)  # поднять вторую выше первой
    after = [r.key for r in menu_service.menu_entries("main", only_enabled=False)]
    assert after[0] == second and after[1] == first


@pytest.mark.asyncio
async def test_caption_override(db):
    assert menu_service.caption("main", "DEF") == "DEF"
    await menu_service.set_caption(db, "main", "Привет!")
    assert menu_service.caption("main", "DEF") == "Привет!"
    await menu_service.set_caption(db, "main", None)
    assert menu_service.caption("main", "DEF") == "DEF"
