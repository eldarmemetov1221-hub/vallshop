"""Уведомления администраторам магазина."""

from __future__ import annotations

import logging
from typing import Iterable

from aiogram import Bot

log = logging.getLogger("vallshop.notify")


async def notify_admins(bot: Bot, admin_ids: Iterable[int], text: str) -> None:
    """Отправить сообщение всем администраторам (ошибки игнорируем)."""
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001 — один недоступный админ не должен ломать рассылку
            log.warning("Не удалось уведомить админа %s", admin_id)
