"""Статистика: продажи, выручка, себестоимость, прибыль, пользователи."""

from decimal import Decimal

import pytest

from bot.db import Database, Product, Variant
from bot.db.models import Order, OrderStatus, User
from bot.services import stats as stats_service


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    yield database
    await database.dispose()


@pytest.mark.asyncio
async def test_sales_and_profit(db):
    async with db.session() as s:
        p = Product(game="X", title="T")
        s.add(p)
        await s.flush()
        v = Variant(
            product_id=p.id, title="v", liog_product_id=1, liog_variation_id=1,
            cost_usd=Decimal("2.00"),
        )
        s.add(v)
        await s.flush()
        s.add(User(id=1))
        # завершённый заказ: 2 шт по 5.00, себестоимость 2.00
        s.add(Order(client_ref="a", user_id=1, variant_id=v.id,
                    price_usd=Decimal("5.00"), quantity=2, status=OrderStatus.COMPLETED))
        # не завершённый — не считается
        s.add(Order(client_ref="b", user_id=1, variant_id=v.id,
                    price_usd=Decimal("5.00"), quantity=1, status=OrderStatus.AWAITING_PAYMENT))
        await s.commit()

    async with db.session() as s:
        st = await stats_service.sales_stats(s, None, __import__("datetime").datetime.utcnow().replace(year=2100))
        assert st["orders"] == 1
        assert st["items"] == 2
        assert st["revenue"] == Decimal("10.00")   # 5*2
        assert st["cost"] == Decimal("380.00")      # 2*2 USDT × курс 95
        assert st["cost_usd"] == Decimal("4.00")
        assert st["profit"] == Decimal("-370.00")
        us = await stats_service.users_stats(s, None, __import__("datetime").datetime.utcnow().replace(year=2100))
        assert us["total"] == 1 and us["new"] == 1


def test_preset_labels():
    _, _, lbl = stats_service.preset_range("today")
    assert lbl == "Сегодня"
    s, _, lbl2 = stats_service.preset_range("all")
    assert s is None and lbl2 == "Всё время"
