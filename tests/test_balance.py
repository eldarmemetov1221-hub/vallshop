"""Тесты баланса и покупки с баланса (in-memory SQLite)."""

from decimal import Decimal

import pytest

from bot.db import Database, Product, Variant
from bot.db.models import Order, OrderStatus
from bot.services import balance as balance_service
from bot.services import orders as order_service
from bot.services import stock as stock_service
from fazercard import FazerCardClient


async def _setup_fzr(db, balance="10", kind="gamekey"):
    async with db.session() as s:
        p = Product(game="Steam", title="Steam")
        s.add(p)
        await s.flush()
        v = Variant(
            product_id=p.id, title="Steam 10$",
            liog_product_id=0, liog_variation_id=-1, cost_usd=Decimal("8.00"),
            source="fazercard", fzr_kind=kind, fzr_a="cat", fzr_b="card",
        )
        s.add(v)
        await s.flush()
        await order_service.ensure_user(s, 7, "u", "U")
        if Decimal(balance) > 0:
            await balance_service.credit(s, 7, Decimal(balance))
        await s.commit()
        return v.id


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


@pytest.mark.asyncio
async def test_fazercard_purchase_delivers_instantly(db):
    vid = await _setup_fzr(db, balance="20")
    fzr = FazerCardClient(mock=True)
    async with db.session() as s:
        v = await s.get(Variant, vid)
        order, codes = await order_service.purchase_from_balance(
            s, user_id=7, variant=v, unit_price=Decimal("10"), quantity=2, fzr=fzr
        )
        await s.commit()
        assert order.status == OrderStatus.COMPLETED
        assert order.supplier == "fazercard"
        assert codes == ["MOCK-1", "MOCK-2"]
        assert await balance_service.get_balance(s, 7) == Decimal("0.00")


class _FailFzr:
    """Поставщик, отклоняющий заказ (терминальный failed)."""

    def order_gamekey(self, **kwargs):
        return {"ok": True, "order": {"id": "x1", "status": "failed"}}


@pytest.mark.asyncio
async def test_fazercard_terminal_failure_rolls_back(db):
    vid = await _setup_fzr(db, balance="10")
    async with db.session() as s:
        v = await s.get(Variant, vid)
        with pytest.raises(order_service.SupplierError):
            await order_service.purchase_from_balance(
                s, user_id=7, variant=v, unit_price=Decimal("10"),
                quantity=1, fzr=_FailFzr(),
            )
        await s.rollback()
        assert await balance_service.get_balance(s, 7) == Decimal("10.00")


class _PendingFzr:
    """Поставщик, который принимает заказ в обработку (pending)."""

    def __init__(self):
        self.polls = 0

    def order_gamekey(self, **kwargs):
        return {"ok": True, "order": {"id": "p1", "status": "processing"}}

    def get_order(self, order_id):
        self.polls += 1
        return {"ok": True, "order": {"id": order_id, "status": "completed", "keys": ["LATE-1"]}}


class _StarsFzr:
    """Telegram Stars: ловит вызов stars-эндпоинта, завершает без кода."""

    def __init__(self):
        self.called = None

    def order_telegram_stars(self, *, telegram_username, quantity, idempotency_key=None):
        self.called = (telegram_username, quantity)
        return {"ok": True, "order": {"id": "tg1", "status": "completed"}}


@pytest.mark.asyncio
async def test_telegram_stars_uses_stars_endpoint(db):
    async with db.session() as s:
        p = Product(game="TG", title="Telegram Stars")
        s.add(p)
        await s.flush()
        v = Variant(
            product_id=p.id, title="Telegram Stars · 50 Stars",
            liog_product_id=0, liog_variation_id=-1, cost_usd=Decimal("0.76"),
            source="fazercard", fzr_kind="giftcard", fzr_a="telegram_stars", fzr_b="c50",
        )
        s.add(v)
        await order_service.ensure_user(s, 7, "u", "U")
        await balance_service.credit(s, 7, Decimal("10"))
        await s.commit()
        vid = v.id

    fzr = _StarsFzr()
    async with db.session() as s:
        v = await s.get(Variant, vid)
        order, codes = await order_service.purchase_from_balance(
            s, user_id=7, variant=v, unit_price=Decimal("0.88"), quantity=1,
            fzr=fzr, topup_fields={"telegram_username": "@test"},
        )
        await s.commit()
        assert fzr.called == ("@test", 50)   # username + распознанное кол-во звёзд
        assert order.status == OrderStatus.COMPLETED
        assert codes == []                    # доставка на username, без кода


class _TopupFzr:
    """Топап: принимает поля игрока, завершает без кода (на аккаунт)."""

    def __init__(self):
        self.fields = None

    def order_topup(self, *, category_id, offer_id, fields, idempotency_key=None):
        self.fields = fields
        return {"ok": True, "order": {"id": "t1", "status": "completed"}}


@pytest.mark.asyncio
async def test_fazercard_topup_completes_without_code(db):
    vid = await _setup_fzr(db, balance="10", kind="topup")
    fzr = _TopupFzr()
    async with db.session() as s:
        v = await s.get(Variant, vid)
        order, codes = await order_service.purchase_from_balance(
            s, user_id=7, variant=v, unit_price=Decimal("3"), quantity=1,
            fzr=fzr, topup_fields={"user_id": "12345"},
        )
        await s.commit()
        assert fzr.fields == {"user_id": "12345"}
        assert order.status == OrderStatus.COMPLETED
        assert codes == []  # доставлено на аккаунт, кода нет
        assert await balance_service.get_balance(s, 7) == Decimal("7.00")


@pytest.mark.asyncio
async def test_fazercard_pending_then_poller_completes(db):
    vid = await _setup_fzr(db, balance="10")
    fzr = _PendingFzr()
    async with db.session() as s:
        v = await s.get(Variant, vid)
        order, codes = await order_service.purchase_from_balance(
            s, user_id=7, variant=v, unit_price=Decimal("10"), quantity=1, fzr=fzr
        )
        await s.commit()
        oid = order.id
        assert order.status == OrderStatus.FULFILLING
        assert order.supplier_order_id == "p1"
        assert codes == []

    # Поллер дотягивает заказ до завершения.
    async with db.session() as s:
        fresh = await s.get(Order, oid)
        status = await order_service.poll_fazercard(s, fresh, fzr)
        await s.commit()
        assert status == OrderStatus.COMPLETED
        assert fresh.delivery_code == "LATE-1"
