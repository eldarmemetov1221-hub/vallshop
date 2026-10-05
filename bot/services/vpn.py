"""Выдача VPN-подписок через VPNresellers.

Покупка VPN-номинала (source="vpnresellers"):
  1) создаём аккаунт у VPNresellers, выставляем срок (expire = сегодня + N дней);
  2) собираем подписку (base64 из vless:// по выбранным серверам);
  3) сохраняем VpnSubscription(token) и отдаём клиенту ссылку /sub/<token>.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Order, OrderStatus, VpnSubscription
from ..services import settings as settings_service
from .orders import SupplierError

log = logging.getLogger("vallshop.vpn")

DEFAULT_DAYS = 30


def _token() -> str:
    return secrets.token_urlsafe(16)


async def selected_server_ids(session: AsyncSession) -> List[str]:
    raw = await settings_service.get(session, settings_service.VPN_SERVER_IDS, "")
    out: List[str] = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part:
            out.append(part)
    return out


def _days_from_variant(variant) -> int:
    # Срок подписки хранится в fzr_a (переиспользуем поле), дефолт 30.
    try:
        return int(variant.fzr_a)
    except (TypeError, ValueError):
        return DEFAULT_DAYS


async def fulfill(
    session: AsyncSession,
    order: Order,
    variant,
    vpn,
    public_base_url: Optional[str],
) -> str:
    """Создать VPN-аккаунт и подписку. Возвращает ссылку /sub/<token>.

    Бросает SupplierError при любой проблеме — вызывающий код откатит/вернёт.
    """
    if vpn is None or not getattr(vpn, "configured", False):
        raise SupplierError("VPN-провайдер не настроен")
    server_ids = await selected_server_ids(session)
    if not server_ids:
        raise SupplierError("Не выбраны VPN-серверы (админ → VPN)")

    days = _days_from_variant(variant)
    expire = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")

    import asyncio
    from vpnresellers import gen_password, gen_username
    from vpnresellers.client import VPNResellersError

    username, password = gen_username(), gen_password()
    try:
        acc = await asyncio.to_thread(
            vpn.create_account, username=username, password=password, expire=expire
        )
        account_id = acc.get("id")
        if not account_id:
            raise SupplierError("VPNresellers не вернул аккаунт")
        config_b64 = await asyncio.to_thread(
            vpn.build_subscription, account_id=account_id, server_ids=server_ids
        )
    except VPNResellersError as e:
        raise SupplierError(str(e))
    if not config_b64:
        raise SupplierError("Не удалось собрать конфигурацию VPN")

    token = _token()
    session.add(VpnSubscription(
        token=token,
        order_id=order.id,
        user_id=order.user_id,
        account_id=str(account_id),
        username=username,
        config_b64=config_b64,
        expires_at=datetime.utcnow() + timedelta(days=days),
    ))
    base = (public_base_url or "").rstrip("/")
    link = f"{base}/sub/{token}" if base else f"/sub/{token}"
    order.supplier = "vpnresellers"
    order.supplier_order_id = str(account_id)
    order.delivery_code = link
    order.status = OrderStatus.COMPLETED
    await session.flush()
    return link


async def get_subscription(session: AsyncSession, token: str) -> Optional[VpnSubscription]:
    from sqlalchemy import select
    return await session.scalar(
        select(VpnSubscription).where(VpnSubscription.token == token)
    )
