"""Жизненный цикл заказа: создание, оплата, выдача (сток + топап-фолбэк).

Схема выдачи:
  1. Основной путь — выдать код из собственного стока (мгновенно).
  2. Фолбэк — если стока нет, создать заказ у поставщика LioGames
     (`order_create`) и опрашивать статус до финала (`poll_topup`),
     учитывая лимит поставщика ~1 заказ / 60 сек.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from liogames import LioGamesClient

from ..db.models import Order, OrderStatus, User, Variant
from . import stock as stock_service


def new_client_ref() -> str:
    """Уникальный client_ref для идемпотентности у поставщика."""
    return "vs-" + uuid.uuid4().hex[:16]


async def ensure_user(
    session: AsyncSession, user_id: int, username: Optional[str], full_name: Optional[str]
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id, username=username, full_name=full_name)
        session.add(user)
        await session.flush()
    else:
        user.username = username or user.username
        user.full_name = full_name or user.full_name
    return user


async def create_order(
    session: AsyncSession, *, user_id: int, variant: Variant, price_usd: Decimal
) -> Order:
    order = Order(
        client_ref=new_client_ref(),
        user_id=user_id,
        variant_id=variant.id,
        price_usd=price_usd,
        status=OrderStatus.CREATED,
    )
    session.add(order)
    await session.flush()
    return order


# ──────────────────────────────────────────────────────────────────────────
# Выдача
# ──────────────────────────────────────────────────────────────────────────
async def fulfill_from_stock(session: AsyncSession, order: Order) -> Optional[str]:
    """Попытаться выдать код из стока. Возвращает код либо None."""
    item = await stock_service.reserve_one(session, order.variant_id, order.id)
    if item is None:
        return None
    await stock_service.mark_sold(session, item)
    order.delivery_code = item.code
    order.status = OrderStatus.COMPLETED
    await session.flush()
    return item.code


async def start_topup(
    session: AsyncSession, order: Order, variant: Variant, liog: LioGamesClient
) -> None:
    """Фолбэк: создать заказ у поставщика. client_ref = order.client_ref."""
    order.status = OrderStatus.FULFILLING
    await session.flush()
    data = liog.order_create(
        variant.liog_variation_id,
        client_ref=order.client_ref,
        product_id=variant.liog_product_id,
    )
    liog_order_id = data.get("order_id")
    if liog_order_id is not None:
        order.liog_order_id = str(liog_order_id)
    # Возможно, код уже готов сразу.
    code = LioGamesClient.extract_code(data)
    if code and LioGamesClient.status_is_terminal_ok(data):
        order.delivery_code = code
        order.status = OrderStatus.COMPLETED
    await session.flush()


async def poll_topup(
    session: AsyncSession, order: Order, liog: LioGamesClient
) -> OrderStatus:
    """Опросить статус топап-заказа и обновить order. Возвращает новый статус."""
    data = liog.order_status(
        order_id=order.liog_order_id or None,
        client_ref=order.client_ref,
    )
    if LioGamesClient.status_is_terminal_ok(data):
        code = LioGamesClient.extract_code(data)
        if code:
            order.delivery_code = code
            order.status = OrderStatus.COMPLETED
        else:
            order.status = OrderStatus.FAILED
    elif LioGamesClient.status_is_terminal_failed(data):
        order.status = OrderStatus.FAILED
    # иначе остаётся FULFILLING (PROCESSING)
    await session.flush()
    return order.status


async def fulfill(
    session: AsyncSession, order: Order, variant: Variant, liog: LioGamesClient
) -> OrderStatus:
    """Полный цикл выдачи после подтверждения оплаты.

    Сначала сток, затем — топап-фолбэк. Если топап не завершился мгновенно,
    статус остаётся FULFILLING и его дотянет фоновый поллер.
    """
    code = await fulfill_from_stock(session, order)
    if code is not None:
        return OrderStatus.COMPLETED

    await start_topup(session, order, variant, liog)
    return order.status
