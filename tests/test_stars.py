"""Telegram Звёзды (FazerCards): котировка-мок, умная цена, покупка, статистика."""

import datetime
from decimal import Decimal

import pytest

from bot.db import Database, Product, Variant
from bot.db.models import Order, OrderStatus, User
from bot.services import balance as balance_service
from bot.services import orders as order_service
from bot.services import settings as settings_service
from bot.services import stars as stars_service
from bot.services import stats as stats_service
from fazercard import FazerCardClient


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    yield database
    await database.dispose()


def test_client_stars_quote_mock():
    c = FazerCardClient(mock=True)
    q = c.telegram_stars_quote()
    assert q["ok"] is True
    assert q["price_per_star"] == "0.0152600"
    assert q["min_amount"] == 50 and q["max_amount"] == 10000
    order = c.order_telegram_stars(telegram_username="user", quantity=100)
    assert order["order"]["status"] == "completed"


def test_stars_price_and_cost():
    pps = Decimal("0.0152600")
    # 100 звёзд: cost = 1.526 USD × 95 × 1.10 = 159.4669 → округл.вверх до 1 ₽ = 160
    price = stars_service.price_rub(100, pps, markup=Decimal("10"), our_rate=Decimal("95"))
    assert price == Decimal("160")
    assert stars_service.cost_usd(100, pps) == Decimal("1.5260000")
    # 50 звёзд (<100 ₽): cost = 0.763 × 95 × 1.10 = 79.73 → округл.вверх до 10 коп = 79.80
    cheap = stars_service.price_rub(50, pps, markup=Decimal("10"), our_rate=Decimal("95"))
    assert cheap == Decimal("79.80")


def test_parse_quote_fallback():
    pps, lo, hi = stars_service._parse_quote({"price_per_star": "0.02", "min_amount": 60, "max_amount": 5000})
    assert pps == Decimal("0.02") and lo == 60 and hi == 5000
    # битый ответ → дефолты
    pps2, lo2, hi2 = stars_service._parse_quote(None)
    assert lo2 == 50 and hi2 == 10000


@pytest.mark.asyncio
async def test_stars_limits_override_and_clamp(db):
    async with db.session() as s:
        # по умолчанию — лимиты API
        lo, hi = await stars_service.get_limits(s, api_min=50, api_max=10000)
        assert (lo, hi) == (50, 10000)
        # переопределение в допустимых границах
        await settings_service.set(s, settings_service.STARS_MIN_QTY, "100")
        await settings_service.set(s, settings_service.STARS_MAX_QTY, "5000")
        await s.commit()
        lo, hi = await stars_service.get_limits(s, api_min=50, api_max=10000)
        assert (lo, hi) == (100, 5000)
        # нельзя выйти за жёсткие лимиты поставщика
        await settings_service.set(s, settings_service.STARS_MAX_QTY, "999999")
        await s.commit()
        lo, hi = await stars_service.get_limits(s, api_min=50, api_max=10000)
        assert hi == 10000


@pytest.mark.asyncio
async def test_stars_purchase_completes_and_counts_cost(db):
    fzr = FazerCardClient(mock=True)
    async with db.session() as s:
        s.add(User(id=5))
        await balance_service.credit(s, 5, Decimal("1000"))
        p = Product(game="TG_STARS", title="Телеграм Звёзды")
        s.add(p); await s.flush()
        v = Variant(
            product_id=p.id, title="Telegram Звёзды",
            liog_product_id=0, liog_variation_id=-1, cost_usd=Decimal("0"),
            source="fazercard", fzr_kind="stars", fzr_a="telegram_stars",
        )
        s.add(v); await s.flush()
        await s.commit()
        vid = v.id

    async with db.session() as s:
        v = await s.get(Variant, vid)
        order, codes = await order_service.purchase_from_balance(
            s, user_id=5, variant=v, unit_price=Decimal("160"), quantity=1, fzr=fzr,
            topup_fields={
                "telegram_username": "someuser", "stars_qty": 100,
                "_cost_usd": "1.5260",
            },
        )
        await s.commit()
        assert order.status == OrderStatus.COMPLETED
        assert codes == []  # выдача без кода (зачисление на аккаунт)
        assert order.cost_usd == Decimal("1.5260")
        assert await balance_service.get_balance(s, 5) == Decimal("840.00")  # 1000-160

    # Статистика: себестоимость берётся из Order.cost_usd.
    async with db.session() as s:
        st = await stats_service.sales_stats(
            s, None, datetime.datetime.utcnow().replace(year=2100)
        )
        assert st["revenue"] == Decimal("160")
        assert st["cost_usd"] == Decimal("1.5260")
