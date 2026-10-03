"""Единый вывод экранов с баннером-картинкой.

Все основные экраны бота — это фото-сообщение (баннер) с подписью и inline-
кнопками под ним. Навигация редактирует это же сообщение (edit_media /
edit_caption). file_id баннеров кешируется, чтобы не пере-загружать картинку.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional, Union

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

log = logging.getLogger("vallshop.ui")

_ASSETS = Path(__file__).parent / "assets"

# Логические имена баннеров -> файл.
BANNERS = {
    "catalog": _ASSETS / "banner_catalog.jpg",
    "profile": _ASSETS / "banner_profile.jpg",
}

# Кеш file_id уже загруженных баннеров (ускоряет повторную отправку).
_file_ids: Dict[str, str] = {}

CAPTION_LIMIT = 1024


def _clip(caption: str) -> str:
    return caption if len(caption) <= CAPTION_LIMIT else caption[: CAPTION_LIMIT - 1] + "…"


def _media_source(banner: str) -> Union[str, FSInputFile]:
    if banner in _file_ids:
        return _file_ids[banner]
    return FSInputFile(str(BANNERS[banner]))


def _remember(banner: str, msg: Message) -> None:
    try:
        if msg.photo:
            _file_ids[banner] = msg.photo[-1].file_id
    except Exception:  # noqa: BLE001
        pass


async def render(
    event: Union[CallbackQuery, Message],
    *,
    banner: str,
    caption: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    """Показать экран с баннером. Для callback — редактирует сообщение,
    для message — отправляет новое фото."""
    caption = _clip(caption)

    if isinstance(event, CallbackQuery):
        msg = event.message
        media = InputMediaPhoto(media=_media_source(banner), caption=caption, parse_mode="HTML")
        try:
            edited = await msg.edit_media(media, reply_markup=reply_markup)
            if isinstance(edited, Message):
                _remember(banner, edited)
            return
        except TelegramBadRequest as exc:
            text = str(exc).lower()
            if "message is not modified" in text:
                return
            # Сообщение без медиа (текст) — удалим и отправим фото заново.
            try:
                await msg.delete()
            except Exception:  # noqa: BLE001
                pass

    target = event.message if isinstance(event, CallbackQuery) else event
    sent = await target.answer_photo(
        _media_source(banner), caption=caption, reply_markup=reply_markup
    )
    _remember(banner, sent)
