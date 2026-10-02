"""HTTP-сервер для вебхуков платёжного провайдера (BoltUtil).

Запускается, только если задан PUBLIC_BASE_URL. Проверяет HMAC-подпись,
находит платёж, помечает оплаченным, запускает выдачу и уведомляет покупателя.
Эндпоинт идемпотентен: повторный вебхук по уже обработанному заказу — no-op.
"""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot
from aiohttp import web
from sqlalchemy import select

from liogames import LioGamesClient

from .db import Database
from .db.models import Order, OrderStatus, Payment, PaymentStatus
from .payments import PaymentProvider
from .services import catalog as catalog_service
from .services import orders as order_service
from . import texts

log = logging.getLogger("vallshop.web")


def build_app(
    *, bot: Bot, db: Database, provider: PaymentProvider, liog: LioGamesClient
) -> web.Application:
    app = web.Application()

    async def healthz(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def bolt_webhook(request: web.Request) -> web.Response:
        raw = await request.read()
        if not provider.verify_webhook(raw, dict(request.headers)):
            log.warning("Вебхук с неверной подписью отклонён")
            return web.json_response({"ok": False}, status=403)

        update = provider.parse_webhook(raw)
        # Отвечаем 2xx всегда после валидной подписи, обработку делаем идемпотентно.
        await _apply_payment(bot, db, liog, update)
        return web.json_response({"ok": True})

    app.router.add_get("/healthz", healthz)
    app.router.add_post("/bolt/webhook", bolt_webhook)
    return app


async def _apply_payment(bot, db: Database, liog, update) -> None:
    if update.status != "paid":
        return
    async with db.session() as session:
        payment = None
        if update.provider_order_id:
            payment = await session.scalar(
                select(Payment).where(
                    Payment.provider_order_id == update.provider_order_id
                )
            )
        if payment is None and update.client_ref:
            order = await session.scalar(
                select(Order).where(Order.client_ref == update.client_ref)
            )
            payment = order.payment if order else None
        if payment is None:
            log.warning("Вебхук: платёж не найден (%s)", update.provider_order_id)
            return

        order = await session.get(Order, payment.order_id)
        if order is None or order.status in (
            OrderStatus.COMPLETED,
            OrderStatus.FULFILLING,
        ):
            return  # уже обработано

        payment.status = PaymentStatus.PAID
        payment.tx_hash = update.tx_hash
        payment.paid_at = datetime.utcnow()
        order.status = OrderStatus.PAID
        await session.flush()

        variant = await catalog_service.get_variant(session, order.variant_id)
        status = await order_service.fulfill(session, order, variant, liog)
        user_id = order.user_id
        code = order.delivery_code
        ref = order.client_ref
        await session.commit()

    if status == OrderStatus.COMPLETED and code:
        await _notify(bot, user_id, texts.DELIVERY_SUCCESS.format(code=code))
    elif status == OrderStatus.FULFILLING:
        await _notify(bot, user_id, texts.FULFILLING)
    else:
        await _notify(bot, user_id, texts.FULFILL_FAILED.format(ref=ref))


async def _notify(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text)
    except Exception:  # noqa: BLE001
        log.warning("Не удалось уведомить user=%s", user_id)
