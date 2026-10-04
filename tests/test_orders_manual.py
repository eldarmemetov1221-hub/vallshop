"""Раздел «Текущие заказы»: автовозврат-переключатель, ручная выдача/отмена."""

from decimal import Decimal

import pytest

from bot.db import Database, Product, Variant
from bot.db.models import Order, OrderStatus
from bot.services import balance as balance_service
from bot.services import orders as order_service
from bot.services import settings as settings_service


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    yield database
    await database.dispose()


async def _setup_fzr(db, balance="10"):
    async with db.session() as s:
        p = Product(game="Steam", title="Steam")
        s.add(p)
        await s.flush()
        v = Variant(
            product_id=p.id, title="Steam 10$",
            liog_product_id=0, liog_variation_id=-1, cost_usd=Decimal("8.00"),
            source="fazercard", fzr_kind="gamekey", fzr_a="cat", fzr_b="card",
        )
        s.add(v)
        await s.flush()
        await order_service.ensure_user(s, 7, "u", "U")
        await balance_service.credit(s, 7, Decimal(balance))
        await s.commit()
        return v.id


class _FailFzr:
    def order_gamekey(self, **kwargs):
        return {"ok": True, "order": {"id": "x1", "status": "failed"}}


# ── Настройки ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_settings_default_and_set(db):
    async with db.session() as s:
        assert await settings_service.get_bool(s, settings_service.AUTO_REFUND, True) is True
        await settings_service.set(s, settings_service.AUTO_REFUND, "0")
        await s.commit()
    async with db.session() as s:
        assert await settings_service.get_bool(s, settings_service.AUTO_REFUND, True) is False


def test_settings_fmt():
    out = settings_service.fmt("заказ {ref} на {amount}", ref="vs-1", amount="5 USDT")
    assert out == "заказ vs-1 на 5 USDT"
    # без плейсхолдеров — текст как есть
    assert settings_service.fmt("просто текст") == "просто текст"


# ── Автовозврат ВЫКЛ → заказ уходит в ручную обработку (деньги списаны) ───────
@pytest.mark.asyncio
async def test_autorefund_off_keeps_money_and_needs_action(db):
    vid = await _setup_fzr(db, balance="10")
    async with db.session() as s:
        await settings_service.set(s, settings_service.AUTO_REFUND, "0")
        await s.commit()
    async with db.session() as s:
        v = await s.get(Variant, vid)
        order, codes = await order_service.purchase_from_balance(
            s, user_id=7, variant=v, unit_price=Decimal("10"),
            quantity=1, fzr=_FailFzr(),
        )
        await s.commit()
        assert order.status == OrderStatus.NEEDS_ACTION
        assert codes == []
        # деньги остаются списанными (0), не возвращены
        assert await balance_service.get_balance(s, 7) == Decimal("0.00")
        assert order.fail_reason


# ── Автовозврат ВКЛ → как раньше: ошибка пробрасывается, деньги откатываются ──
@pytest.mark.asyncio
async def test_autorefund_on_raises(db):
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


# ── Ручная выдача кода ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_manual_deliver_code(db):
    vid = await _setup_fzr(db, balance="10")
    async with db.session() as s:
        await settings_service.set(s, settings_service.AUTO_REFUND, "0")
        await s.commit()
    async with db.session() as s:
        v = await s.get(Variant, vid)
        order, _ = await order_service.purchase_from_balance(
            s, user_id=7, variant=v, unit_price=Decimal("10"), quantity=1, fzr=_FailFzr(),
        )
        await s.commit()
        oid = order.id
    async with db.session() as s:
        order = await s.get(Order, oid)
        await order_service.deliver_code_manual(s, order, "CODE-123")
        await s.commit()
    async with db.session() as s:
        order = await s.get(Order, oid)
        assert order.status == OrderStatus.COMPLETED
        assert order.delivery_code == "CODE-123"
        assert order.fail_reason is None


# ── Ручная отмена → возврат денег ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_manual_cancel_refunds(db):
    vid = await _setup_fzr(db, balance="10")
    async with db.session() as s:
        await settings_service.set(s, settings_service.AUTO_REFUND, "0")
        await s.commit()
    async with db.session() as s:
        v = await s.get(Variant, vid)
        order, _ = await order_service.purchase_from_balance(
            s, user_id=7, variant=v, unit_price=Decimal("10"), quantity=1, fzr=_FailFzr(),
        )
        await s.commit()
        oid = order.id
        assert await balance_service.get_balance(s, 7) == Decimal("0.00")
    async with db.session() as s:
        order = await s.get(Order, oid)
        refund = await order_service.cancel_order_manual(s, order)
        await s.commit()
        assert refund == Decimal("10.00")
    async with db.session() as s:
        order = await s.get(Order, oid)
        assert order.status == OrderStatus.REFUNDED
        assert await balance_service.get_balance(s, 7) == Decimal("10.00")
