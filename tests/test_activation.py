"""Активация кодов: расчёт цены Roblox и сервис кодов/заявок."""

import pytest

import roblox
from bot.db import Database
from bot.services import activation as act


def test_expected_price_table():
    assert roblox.parse_robux_amount("100Robux") == 100
    assert roblox.expected_gamepass_price(100) == 143
    assert roblox.expected_gamepass_price(400) == 572
    assert roblox.expected_gamepass_price(700) == 1000
    assert roblox.expected_gamepass_price(1000) == 1429
    assert roblox.expected_gamepass_price(None) is None


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    yield database
    await database.dispose()


@pytest.mark.asyncio
async def test_add_find_and_lifecycle(db):
    async with db.session() as s:
        ok = await act.add_code(
            s, code="123-456-789", product="100Robux",
            instruction="инструкция", robux_amount=100,
        )
        assert ok
        # дубль не добавляется
        assert await act.add_code(s, code="123-456-789", product="x", instruction=None, robux_amount=None) is False
        await s.commit()

    async with db.session() as s:
        c = await act.find_code(s, "123-456-789")
        assert c and c.status == "free" and c.robux_amount == 100
        await act.mark_used(s, "123-456-789", 777)
        await s.commit()

    async with db.session() as s:
        c = await act.find_code(s, "123-456-789")
        assert c.status == "used" and c.used_by == 777
        counts = await act.code_counts(s)
        assert counts == {"total": 1, "free": 0, "used": 1}
        # возврат в свободные
        await act.set_free(s, "123-456-789")
        await s.commit()
        c = await act.find_code(s, "123-456-789")
        assert c.status == "free" and c.used_by is None


@pytest.mark.asyncio
async def test_request_decision(db):
    async with db.session() as s:
        req = await act.create_request(
            s, code="123-456-789", product="100Robux", user_id=5,
            username="@u", nickname="nick", expected_price=143, actual_price=100,
        )
        await s.commit()
        rid = req.id
        assert req.status == "review"

    async with db.session() as s:
        r = await act.decide_request(s, rid, "approved")
        await s.commit()
        assert r.status == "approved" and r.decided_at is not None
