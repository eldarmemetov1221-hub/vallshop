"""Жизненный цикл заказа: создание, оплата, выдача (сток + топап-фолбэк).

Схема выдачи:
  1. Основной путь — выдать код из собственного стока (мгновенно).
  2. Фолбэк — если стока нет, создать заказ у поставщика LioGames
     (`order_create`) и опрашивать статус до финала (`poll_topup`),
     учитывая лимит поставщика ~1 заказ / 60 сек.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from liogames import LioGamesClient
from fazercard import FazerCardClient, FazerCardError

from ..db.models import Order, OrderStatus, User, Variant
from . import balance as balance_service
from . import referral as referral_service
from . import settings as settings_service
from . import stock as stock_service


class PurchaseError(Exception):
    """Базовая ошибка покупки."""


class OutOfStock(PurchaseError):
    pass


class InsufficientBalance(PurchaseError):
    pass


class SupplierError(PurchaseError):
    """Ошибка на стороне поставщика buy-on-demand (FazerCard и т.п.)."""


def _first_int(text: Optional[str]) -> Optional[int]:
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


def _no_code_delivery(variant: Variant) -> bool:
    """Доставка без кода (на аккаунт/username): топапы и Telegram Stars/Premium."""
    return variant.fzr_a in ("telegram_stars", "telegram_premium")


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
    session: AsyncSession,
    *,
    user_id: int,
    variant: Variant,
    price_usd: Decimal,
    quantity: int = 1,
) -> Order:
    order = Order(
        client_ref=new_client_ref(),
        user_id=user_id,
        variant_id=variant.id,
        price_usd=price_usd,
        quantity=quantity,
        status=OrderStatus.CREATED,
    )
    session.add(order)
    await session.flush()
    return order


async def purchase_from_balance(
    session: AsyncSession,
    *,
    user_id: int,
    variant: Variant,
    unit_price: Decimal,
    quantity: int,
    fzr: Optional[FazerCardClient] = None,
    topup_fields: Optional[dict] = None,
    vpn=None,
    public_base_url: Optional[str] = None,
) -> tuple[Order, list[str]]:
    """Купить quantity единиц с баланса и выдать коды.

    Источник выдачи зависит от ``variant.source``:
      * ``stock`` / ``liogames`` — мгновенно из собственного стока;
      * ``fazercard`` — заказ у поставщика FazerCard (buy-on-demand). Если код
        выдан сразу — заказ COMPLETED; если поставщик обрабатывает — заказ
        остаётся FULFILLING и его дотянет поллер.

    Бросает OutOfStock / InsufficientBalance / SupplierError. Вызывающий код
    коммитит при успехе и откатывает при исключении (откат возвращает баланс).
    """
    total = Decimal(unit_price) * quantity

    order = await create_order(
        session,
        user_id=user_id,
        variant=variant,
        price_usd=unit_price,
        quantity=quantity,
    )

    # 1) Списываем деньги (атомарно, только если хватает).
    if not await balance_service.try_debit(session, user_id, total):
        raise InsufficientBalance()

    # 2) Выдача по источнику.
    if variant.source == "fazercard":
        return await _fulfill_fazercard(session, order, variant, quantity, fzr, topup_fields)

    if variant.source == "vpnresellers":
        from . import vpn as vpn_service  # ленивый импорт (избегаем цикла)
        link = await vpn_service.fulfill(session, order, variant, vpn, public_base_url)
        await referral_service.accrue_for_order(session, order)
        return order, [link]

    # Сток (свой товар / LioGames): резервируем нужное число кодов.
    items = await stock_service.reserve_many(session, variant.id, order.id, quantity)
    if len(items) != quantity:
        raise OutOfStock()

    # Помечаем проданными и формируем выдачу.
    codes: list[str] = []
    for item in items:
        await stock_service.mark_sold(session, item)
        codes.append(item.code)

    order.delivery_code = "\n".join(codes)
    order.status = OrderStatus.COMPLETED
    await session.flush()
    await referral_service.accrue_for_order(session, order)
    return order, codes


async def _fulfill_fazercard(
    session: AsyncSession,
    order: Order,
    variant: Variant,
    quantity: int,
    fzr: Optional[FazerCardClient],
    topup_fields: Optional[dict],
) -> tuple[Order, list[str]]:
    """Оформить заказ у FazerCard. Коды — сразу или через поллер (FULFILLING)."""
    if fzr is None:
        raise SupplierError("FazerCard не настроен")

    order.supplier = "fazercard"
    order.status = OrderStatus.FULFILLING
    await session.flush()

    kind = variant.fzr_kind
    fields = topup_fields or {}

    def _call():
        # Telegram Stars / Premium — спец-эндпоинты с получателем (username).
        if variant.fzr_a == "telegram_stars":
            uname = (fields.get("telegram_username") or "").strip()
            amount = _first_int(variant.title)
            if not uname:
                raise SupplierError("Не указан Telegram @username получателя")
            if not amount:
                raise SupplierError("Не удалось определить количество звёзд")
            return fzr.order_telegram_stars(
                telegram_username=uname, quantity=amount, idempotency_key=order.client_ref,
            )
        if variant.fzr_a == "telegram_premium":
            uname = (fields.get("telegram_username") or "").strip()
            months = _first_int(variant.title)
            if not uname:
                raise SupplierError("Не указан Telegram @username получателя")
            return fzr.order_telegram_premium(
                telegram_username=uname, months=months, idempotency_key=order.client_ref,
            )
        if kind == "gamekey":
            return fzr.order_gamekey(
                game_id=variant.fzr_a, key_id=variant.fzr_b,
                quantity=quantity, idempotency_key=order.client_ref,
            )
        if kind == "giftcard":
            return fzr.order_giftcard(
                category_id=variant.fzr_a, card_id=variant.fzr_b,
                quantity=quantity, idempotency_key=order.client_ref,
            )
        if kind == "topup":
            return fzr.order_topup(
                category_id=variant.fzr_a, offer_id=variant.fzr_b,
                fields=fields, idempotency_key=order.client_ref,
            )
        raise SupplierError(f"Неизвестный тип FazerCard: {kind}")

    try:
        data = await asyncio.to_thread(_call)
    except FazerCardError as e:
        # Автовозврат включён — классическое поведение: бросаем ошибку, вызывающий
        # код откатывает списание и показывает «поставщик недоступен».
        # Выключен — деньги остаются списанными, заказ уходит в ручную обработку
        # (раздел «Не выполненные»), покупателю показываем «оформляем…».
        auto_refund = await settings_service.get_bool(
            session, settings_service.AUTO_REFUND, True
        )
        if auto_refund:
            raise SupplierError(str(e))
        order.status = OrderStatus.NEEDS_ACTION
        order.fail_reason = str(e)[:255]
        await session.flush()
        return order, []

    # order_id поставщика (для поллинга и поддержки).
    env = data.get("order") if isinstance(data, dict) else None
    oid = env.get("id") if isinstance(env, dict) else None
    if oid:
        order.supplier_order_id = str(oid)

    codes = FazerCardClient.extract_codes(data)
    if FazerCardClient.status_is_terminal_ok(data):
        if codes:
            order.delivery_code = "\n".join(codes)
            order.status = OrderStatus.COMPLETED
        elif variant.fzr_kind == "topup" or _no_code_delivery(variant):
            # Топап / Telegram Stars|Premium — зачисляется на аккаунт, кода нет.
            order.status = OrderStatus.COMPLETED
        # иначе (ключ/карта без кода) — остаёмся FULFILLING, коды дотянет поллер
        await session.flush()
        if order.status == OrderStatus.COMPLETED:
            await referral_service.accrue_for_order(session, order)
        return order, codes

    if FazerCardClient.status_is_terminal_failed(data):
        auto_refund = await settings_service.get_bool(
            session, settings_service.AUTO_REFUND, True
        )
        if auto_refund:
            raise SupplierError("FazerCard отклонил заказ")
        order.status = OrderStatus.NEEDS_ACTION
        order.fail_reason = "FazerCard отклонил заказ"
        await session.flush()
        return order, []

    # Обрабатывается — оставляем FULFILLING, выдачу дотянет поллер.
    await session.flush()
    return order, []


async def poll_fazercard(
    session: AsyncSession, order: Order, fzr: FazerCardClient
) -> OrderStatus:
    """Опросить статус заказа FazerCard и обновить order. Возвращает статус."""
    if not order.supplier_order_id:
        return order.status
    try:
        data = await asyncio.to_thread(fzr.get_order, order.supplier_order_id)
    except FazerCardError:
        return order.status  # сеть/поставщик недоступны — попробуем позже

    if FazerCardClient.status_is_terminal_ok(data):
        codes = FazerCardClient.extract_codes(data)
        if codes:
            order.delivery_code = "\n".join(codes)
            order.status = OrderStatus.COMPLETED
        else:
            variant = await session.get(Variant, order.variant_id)
            if variant and (variant.fzr_kind == "topup" or _no_code_delivery(variant)):
                order.status = OrderStatus.COMPLETED  # зачислено на аккаунт/username
            else:
                order.status = OrderStatus.FAILED  # ключ/карта без кода — ошибка
    elif FazerCardClient.status_is_terminal_failed(data):
        order.status = OrderStatus.FAILED
    # иначе остаётся FULFILLING
    await session.flush()
    if order.status == OrderStatus.COMPLETED:
        await referral_service.accrue_for_order(session, order)
    return order.status


# ──────────────────────────────────────────────────────────────────────────
# Выдача (старый путь: сток + топап-фолбэк, оплата отдельным счётом)
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
    if order.status == OrderStatus.COMPLETED:
        await referral_service.accrue_for_order(session, order)
    return order.status


# ──────────────────────────────────────────────────────────────────────────
# Ручная выдача / отмена заказа администратором (раздел «Не выполненные»)
# ──────────────────────────────────────────────────────────────────────────
async def deliver_code_manual(
    session: AsyncSession, order: Order, code: str
) -> None:
    """Выдать заказ вручную с кодом: COMPLETED + сохранить код."""
    order.delivery_code = code
    order.status = OrderStatus.COMPLETED
    order.fail_reason = None
    await session.flush()
    await referral_service.accrue_for_order(session, order)


async def deliver_topup_manual(session: AsyncSession, order: Order) -> None:
    """Пометить топап вручную выполненным (без кода, зачислено на аккаунт)."""
    order.status = OrderStatus.COMPLETED
    order.fail_reason = None
    await session.flush()
    await referral_service.accrue_for_order(session, order)


async def cancel_order_manual(session: AsyncSession, order: Order) -> Decimal:
    """Отменить заказ: вернуть деньги на баланс покупателя. Возвращает сумму."""
    refund = Decimal(order.price_usd) * (order.quantity or 1)
    await balance_service.credit(session, order.user_id, refund)
    order.status = OrderStatus.REFUNDED
    await session.flush()
    await referral_service.reverse_for_order(session, order)
    return refund


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
