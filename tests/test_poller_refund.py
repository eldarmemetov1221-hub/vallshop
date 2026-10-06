"""Поллер выдачи: асинхронный возврат поставщика (FazerCard) → возврат баланса.

Регресс на реальный баг Steam-пополнения: заказ ушёл в FULFILLING, поставщик
позже вернул деньги со статусом ``refund`` — бот обязан вернуть баланс
покупателю и уведомить его (а не зависнуть молча).
"""

from decimal import Decimal

import pytest

from bot.db import Database, Product, Variant
from bot.db.models import Order, OrderStatus
from bot.services import balance as balance_service
from bot.services import orders as order_service
from bot.services import poller as poller_service


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    yield database
    await database.dispose()


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.messages.append((chat_id, text))


class _Config:
    admin_ids = [999]


class _RefundFzr:
    """Поставщик: заказ создаётся «в обработке», при опросе — ``refund``."""

    def order_steam_topup(self, **kwargs):
        return {"ok": True, "order": {"id": "ord-1877036", "status": "processing"}}

    def get_order(self, order_id):
        assert order_id == "ord-1877036"
        return {"ok": True, "order": {"id": order_id, "status": "refund"}}


@pytest.mark.asyncio
async def test_async_supplier_refund_credits_balance_and_notifies(db):
    fzr = _RefundFzr()
    # 1) Покупка Steam-пополнения: деньги списаны, заказ ушёл в FULFILLING.
    async with db.session() as s:
        p = Product(game="Steam", title="Steam")
        s.add(p)
        await s.flush()
        v = Variant(
            product_id=p.id, title="Steam",
            liog_product_id=0, liog_variation_id=-1, cost_usd=Decimal("0"),
            source="fazercard", fzr_kind="steam", fzr_a="steam", fzr_b="",
        )
        s.add(v)
        await s.flush()
        await order_service.ensure_user(s, 7, "u", "U")
        await balance_service.credit(s, 7, Decimal("51"))
        order, codes = await order_service.purchase_from_balance(
            s, user_id=7, variant=v, unit_price=Decimal("51"), quantity=1, fzr=fzr,
            topup_fields={"steam_login": "login", "amount": "51", "currency": "RUB"},
        )
        await s.commit()
        assert order.status == OrderStatus.FULFILLING
        assert order.supplier_order_id == "ord-1877036"
        assert codes == []
        # деньги списаны
        assert await balance_service.get_balance(s, 7) == Decimal("0")

    # 2) Фоновый поллер опрашивает поставщика → статус refund → возврат + уведомление.
    bot = _FakeBot()
    await poller_service._tick(bot, db, liog=None, fzr=fzr, config=_Config())

    async with db.session() as s:
        order = await s.get(Order, 1)
        assert order.status == OrderStatus.REFUNDED
        # баланс возвращён покупателю
        assert await balance_service.get_balance(s, 7) == Decimal("51")

    # покупатель получил уведомление о возврате; админ — служебное.
    user_msgs = [t for cid, t in bot.messages if cid == 7]
    admin_msgs = [t for cid, t in bot.messages if cid == 999]
    assert user_msgs and "баланс" in user_msgs[0].lower()
    assert admin_msgs
