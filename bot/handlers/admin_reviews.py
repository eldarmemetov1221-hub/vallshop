"""Админ-раздел «Отзывы»: модерация — редактировать, опубликовать, удалить."""

from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..config import BotConfig
from ..db import Database
from ..services import reviews as review_service
from .. import texts

router = Router(name="admin_reviews")


class IsAdminCb(BaseFilter):
    async def __call__(self, call: CallbackQuery, config: BotConfig) -> bool:
        return bool(call.from_user and config.is_admin(call.from_user.id))


class IsAdminMsg(BaseFilter):
    async def __call__(self, message: Message, config: BotConfig) -> bool:
        return bool(message.from_user and config.is_admin(message.from_user.id))


router.callback_query.filter(IsAdminCb())
router.message.filter(IsAdminMsg())


class ARVState(StatesGroup):
    edit_text = State()


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _stars(rating: int) -> str:
    rating = max(0, min(5, int(rating or 0)))
    return "★" * rating + "☆" * (5 - rating)


_STATUS_RU = {"new": "🆕 новый", "published": "✅ опубликован"}


@router.callback_query(F.data == "arv_home")
async def cb_home(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    async with db.session() as session:
        items = await review_service.list_all(session, limit=40)
        total = await review_service.count_published(session)
    kb = InlineKeyboardBuilder()
    for r in items:
        mark = "✅" if r.status == "published" else "🆕"
        kb.row(_btn(f"{mark} {_stars(r.rating)} · {r.product_title or '—'}", f"arv:{r.id}"))
    kb.row(_btn("⬅️ Назад", "a_home"))
    caption = (
        "⭐ <b>Отзывы</b>\n"
        f"Опубликовано: <b>{total}</b> · всего в списке: <b>{len(items)}</b>\n\n"
        + ("Выберите отзыв для модерации:" if items else "Отзывов пока нет.")
    )
    await call.message.edit_text(caption, reply_markup=kb.as_markup())
    await call.answer()


async def _detail(session, review_id: int) -> tuple[str, InlineKeyboardMarkup] | tuple[None, None]:
    r = await review_service.get(session, review_id)
    if not r:
        return None, None
    who = f"@{r.username}" if r.username else f"id {r.user_id}"
    when = r.created_at.strftime("%d.%m.%Y %H:%M") if r.created_at else "—"
    amount = texts.money(Decimal(r.amount_usd)) if r.amount_usd is not None else "—"
    caption = (
        f"⭐ <b>Отзыв #{r.id}</b> · {_STATUS_RU.get(r.status, r.status)}\n"
        f"Заказ: <code>{r.order_id or '—'}</code>\n"
        f"Дата: {when}\n"
        f"Товар: <b>{r.product_title or '—'}</b>\n"
        f"Покупатель: {who}\n"
        f"Сумма заказа: <b>{amount}</b>\n"
        f"Оценка: {_stars(r.rating)}\n\n"
        f"<b>Текст:</b>\n<blockquote>{r.text or ''}</blockquote>"
    )
    kb = InlineKeyboardBuilder()
    kb.row(_btn("✏️ Редактировать текст", f"arv_edit:{r.id}"))
    if r.status == "published":
        kb.row(_btn("🙈 Снять с публикации", f"arv_unpub:{r.id}"))
    else:
        kb.row(_btn("📢 Опубликовать", f"arv_pub:{r.id}"))
    kb.row(_btn("🗑 Удалить", f"arv_del:{r.id}"))
    kb.row(_btn("⬅️ Назад", "arv_home"))
    return caption, kb.as_markup()


@router.callback_query(F.data.startswith("arv:"))
async def cb_detail(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    rid = int(call.data.split(":", 1)[1])
    async with db.session() as session:
        caption, markup = await _detail(session, rid)
    if not caption:
        await call.answer("Не найдено", show_alert=True)
        return
    await call.message.edit_text(caption, reply_markup=markup)
    await call.answer()


@router.callback_query(F.data.startswith("arv_pub:"))
async def cb_publish(call: CallbackQuery, db: Database) -> None:
    rid = int(call.data.split(":", 1)[1])
    async with db.session() as session:
        await review_service.set_status(session, rid, "published")
        await session.commit()
        caption, markup = await _detail(session, rid)
    await call.message.edit_text(caption, reply_markup=markup)
    await call.answer("Опубликовано ✅")


@router.callback_query(F.data.startswith("arv_unpub:"))
async def cb_unpublish(call: CallbackQuery, db: Database) -> None:
    rid = int(call.data.split(":", 1)[1])
    async with db.session() as session:
        await review_service.set_status(session, rid, "new")
        await session.commit()
        caption, markup = await _detail(session, rid)
    await call.message.edit_text(caption, reply_markup=markup)
    await call.answer("Снято с публикации")


@router.callback_query(F.data.startswith("arv_del:"))
async def cb_delete_ask(call: CallbackQuery) -> None:
    rid = int(call.data.split(":", 1)[1])
    kb = InlineKeyboardBuilder()
    kb.row(_btn("🗑 Да, удалить", f"arv_delok:{rid}"))
    kb.row(_btn("⬅️ Отмена", f"arv:{rid}"))
    await call.message.edit_text(
        "Удалить этот отзыв? Действие необратимо.", reply_markup=kb.as_markup()
    )
    await call.answer()


@router.callback_query(F.data.startswith("arv_delok:"))
async def cb_delete(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    rid = int(call.data.split(":", 1)[1])
    async with db.session() as session:
        await review_service.delete(session, rid)
        await session.commit()
    await cb_home(call, db, state)


@router.callback_query(F.data.startswith("arv_edit:"))
async def cb_edit_ask(call: CallbackQuery, state: FSMContext) -> None:
    rid = int(call.data.split(":", 1)[1])
    await state.set_state(ARVState.edit_text)
    await state.update_data(rid=rid)
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ Отмена", f"arv:{rid}"))
    await call.message.edit_text(
        "✏️ Пришлите новый текст отзыва (можно премиум-эмодзи и форматирование).",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


@router.message(ARVState.edit_text)
async def msg_edit(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    rid = data.get("rid")
    text = (message.html_text or message.text or "").strip()
    if not text:
        await message.answer("Пусто. Пришлите текст.")
        return
    async with db.session() as session:
        await review_service.update_text(session, rid, text)
        await session.commit()
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ К отзыву", f"arv:{rid}"))
    await message.answer("✅ Текст отзыва обновлён", reply_markup=kb.as_markup())
