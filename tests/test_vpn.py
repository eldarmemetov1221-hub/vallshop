"""VPN (VPNresellers): клиент (mock) и выдача подписки."""

import base64
from decimal import Decimal

import pytest

from bot.db import Database, Product, Variant
from bot.db.models import Order, OrderStatus, User, VpnSubscription
from bot.services import balance as balance_service
from bot.services import orders as order_service
from bot.services import settings as settings_service
from vpnresellers import VPNResellersClient


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    yield database
    await database.dispose()


def test_client_build_subscription_mock():
    c = VPNResellersClient(mock=True)
    acc = c.create_account(username="u", password="p", expire="2030-01-01")
    assert acc["id"]
    b64 = c.build_subscription(account_id=acc["id"], server_ids=[1, 2, 3])
    decoded = base64.b64decode(b64).decode()
    assert decoded.count("vless://") == 3


@pytest.mark.asyncio
async def test_vpn_purchase_delivers_subscription(db):
    vpn = VPNResellersClient(mock=True)
    async with db.session() as s:
        s.add(User(id=7))
        await balance_service.credit(s, 7, Decimal("1000"))
        p = Product(game="VPN", title="VPN | VallShop")
        s.add(p); await s.flush()
        v = Variant(
            product_id=p.id, title="VPN 1 мес",
            liog_product_id=0, liog_variation_id=-1, cost_usd=Decimal("1.99"),
            source="vpnresellers", fzr_kind="vpn", fzr_a="30", fzr_b="vless",
        )
        s.add(v); await s.flush()
        await settings_service.set(s, settings_service.VPN_SERVER_IDS, "1,2,3")
        await s.commit()
        vid = v.id

    async with db.session() as s:
        v = await s.get(Variant, vid)
        order, codes = await order_service.purchase_from_balance(
            s, user_id=7, variant=v, unit_price=Decimal("150"), quantity=1,
            vpn=vpn, public_base_url="https://shop.vallshop.com",
        )
        await s.commit()
        assert order.status == OrderStatus.COMPLETED
        assert codes and codes[0].startswith("https://shop.vallshop.com/sub/")
        # баланс списан (1000 - 150)
        assert await balance_service.get_balance(s, 7) == Decimal("850.00")
        sub = await s.scalar(
            __import__("sqlalchemy").select(VpnSubscription)
        )
        assert sub and sub.config_b64
        decoded = base64.b64decode(sub.config_b64).decode()
        assert decoded.count("vless://") == 3


@pytest.mark.asyncio
async def test_vpn_no_servers_fails(db):
    vpn = VPNResellersClient(mock=True)
    async with db.session() as s:
        s.add(User(id=7))
        await balance_service.credit(s, 7, Decimal("1000"))
        p = Product(game="VPN", title="VPN")
        s.add(p); await s.flush()
        v = Variant(
            product_id=p.id, title="VPN", liog_product_id=0, liog_variation_id=-1,
            cost_usd=Decimal("1.99"), source="vpnresellers", fzr_kind="vpn", fzr_a="30",
        )
        s.add(v); await s.flush()
        await s.commit()
        vid = v.id
    async with db.session() as s:
        v = await s.get(Variant, vid)
        with pytest.raises(order_service.SupplierError):
            await order_service.purchase_from_balance(
                s, user_id=7, variant=v, unit_price=Decimal("150"), quantity=1,
                vpn=vpn, public_base_url="https://x",
            )
