"""Пополнение Steam по логину (FazerCards): клиент-мок, цена, покупка, статистика."""

from decimal import Decimal

import pytest

from bot.db import Database, Product, Variant
from bot.db.models import Order, OrderStatus, User
from bot.services import balance as balance_service
from bot.services import orders as order_service
from bot.services import stats as stats_service
from bot.services import steam as steam_service
from fazercard import FazerCardClient


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    yield database
    await database.dispose()


def test_client_steam_mock():
    c = FazerCardClient(mock=True)
    assert c.steam_check_login("someuser")["valid"] is True
    assert c.steam_check_login("invalid")["valid"] is False
    rates = c.steam_rates()["rates"]
    assert rates["RUB"] == "83.5"
    order = c.order_steam_topup(steam_login="u", amount="500", currency="RUB")
    assert order["order"]["status"] == "completed"


def test_steam_price_rub():
    fzr_rates = {"USD": Decimal("1"), "RUB": Decimal("83.5"), "KZT": Decimal("480")}
    # 500 RUB на Steam: 500/83.5 = 5.988 USD × 95 × 1.05 = 597.3 → округл.вверх 598
    price = steam_service.price_rub(
        Decimal("500"), "RUB", fzr_rates, markup=Decimal("5"), our_rate=Decimal("95")
    )
    assert price == Decimal("598")
    # 10 USD: 10 × 95 × 1.05 = 997.5 → 998
    price_usd = steam_service.price_rub(
        Decimal("10"), "USD", fzr_rates, markup=Decimal("5"), our_rate=Decimal("95")
    )
    assert price_usd == Decimal("998")
    # себестоимость USD считается от курса валюты
    assert steam_service.cost_usd(Decimal("480"), "KZT", fzr_rates) == Decimal("1")


@pytest.mark.asyncio
async def test_steam_purchase_completes_and_counts_cost(db):
    fzr = FazerCardClient(mock=True)
    async with db.session() as s:
        s.add(User(id=5))
        await balance_service.credit(s, 5, Decimal("1000"))
        p = Product(game="STEAM_TOPUP", title="Пополнить Steam")
        s.add(p); await s.flush()
        v = Variant(
            product_id=p.id, title="Пополнение Steam",
            liog_product_id=0, liog_variation_id=-1, cost_usd=Decimal("0"),
            source="fazercard", fzr_kind="steam", fzr_a="RUB",
        )
        s.add(v); await s.flush()
        await s.commit()
        vid = v.id

    async with db.session() as s:
        v = await s.get(Variant, vid)
        order, codes = await order_service.purchase_from_balance(
            s, user_id=5, variant=v, unit_price=Decimal("598"), quantity=1, fzr=fzr,
            topup_fields={
                "steam_login": "myuser", "amount": "500",
                "currency": "RUB", "_cost_usd": "5.9880",
            },
        )
        await s.commit()
        assert order.status == OrderStatus.COMPLETED
        assert codes == []  # выдача без кода (зачисление на Steam)
        assert order.cost_usd == Decimal("5.9880")
        assert await balance_service.get_balance(s, 5) == Decimal("402.00")  # 1000-598

    # Статистика: себестоимость берётся из Order.cost_usd (а не Variant.cost_usd=0).
    async with db.session() as s:
        import datetime
        st = await stats_service.sales_stats(
            s, None, datetime.datetime.utcnow().replace(year=2100)
        )
        assert st["revenue"] == Decimal("598")
        assert st["cost_usd"] == Decimal("5.9880")
        # себестоимость в ₽ по курсу 95
        assert st["cost"] == Decimal("5.9880") * Decimal("95")
