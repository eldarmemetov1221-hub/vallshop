"""Раздел «📊 Статистика» в админ-панели (только ADMIN_IDS)."""

from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..config import BotConfig
from ..db import Database
from ..services import stats as stats_service

router = Router()


class IsAdminCb(BaseFilter):
    async def __call__(self, call: CallbackQuery, config: BotConfig) -> bool:
        return bool(call.from_user and config.is_admin(call.from_user.id))


class IsAdminMsg(BaseFilter):
    async def __call__(self, message: Message, config: BotConfig) -> bool:
        return bool(message.from_user and config.is_admin(message.from_user.id))


router.callback_query.filter(IsAdminCb())
router.message.filter(IsAdminMsg())


class StatsFlow(StatesGroup):
    custom = State()


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("Сегодня", "st:today"), _btn("7 дней", "st:7d"))
    kb.row(_btn("30 дней", "st:30d"), _btn("Этот месяц", "st:month"))
    kb.row(_btn("Всё время", "st:all"))
    kb.row(_btn("📅 Свой период", "st_custom"))
    kb.row(_btn("⬅️ В админ-панель", "a_home"))
    return kb.as_markup()


def _fmt(label: str, u: dict, s: dict, cur: str) -> str:
    from .. import texts
    cost_usd = s.get("cost_usd")
    cost_line = texts.rub(s['cost'])
    if cost_usd:
        cost_line += f" (≈ {texts.money(cost_usd)})"
    return (
        f"📊 <b>Статистика</b>\n"
        f"Период: <b>{label}</b>\n\n"
        f"👥 <b>Пользователи</b>\n"
        f"Всего: <b>{u['total']}</b>\n"
        f"Новых за период: <b>{u['new']}</b>\n\n"
        f"🛒 <b>Продажи</b>\n"
        f"Заказов: <b>{s['orders']}</b>\n"
        f"Товаров продано: <b>{s['items']}</b>\n"
        f"Выручка: <b>{texts.rub(s['revenue'])}</b>\n"
        f"Себестоимость: {cost_line}\n"
        f"Прибыль: <b>{texts.rub(s['profit'])}</b>"
    )


async def _render(target, db: Database, config: BotConfig, start, end, label, edit=True):
    async with db.session() as session:
        u = await stats_service.users_stats(session, start, end)
        s = await stats_service.sales_stats(session, start, end)
    text = _fmt(label, u, s, config.currency)
    if edit:
        try:
            await target.edit_text(text, reply_markup=_kb())
            return
        except Exception:  # noqa: BLE001
            pass
    await target.answer(text, reply_markup=_kb())


@router.callback_query(F.data == "st_home")
async def cb_stats_home(call: CallbackQuery, db: Database, config: BotConfig, state: FSMContext) -> None:
    await state.clear()
    start, end, label = stats_service.preset_range("today")
    await _render(call.message, db, config, start, end, label)
    await call.answer()


@router.callback_query(F.data.startswith("st:"))
async def cb_stats_preset(call: CallbackQuery, db: Database, config: BotConfig, state: FSMContext) -> None:
    await state.clear()
    start, end, label = stats_service.preset_range(call.data.split(":", 1)[1])
    await _render(call.message, db, config, start, end, label)
    await call.answer()


@router.callback_query(F.data == "st_custom")
async def cb_stats_custom(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(StatsFlow.custom)
    b = InlineKeyboardBuilder()
    b.row(_btn("⬅️ Назад", "st_home"))
    await call.message.edit_text(
        "📅 Пришлите период:\n"
        "• один день — <code>05.10.2026</code>\n"
        "• диапазон — <code>01.10.2026 - 05.10.2026</code>",
        reply_markup=b.as_markup(),
    )
    await call.answer()


@router.message(StatsFlow.custom)
async def msg_stats_custom(message: Message, db: Database, config: BotConfig, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    parts = [p.strip() for p in raw.replace("—", "-").split("-")]
    try:
        if len(parts) == 1:
            d = datetime.strptime(parts[0], "%d.%m.%Y")
            start, end = stats_service.day_bounds(d)
            label = parts[0]
        elif len(parts) == 2:
            d1 = datetime.strptime(parts[0], "%d.%m.%Y")
            d2 = datetime.strptime(parts[1], "%d.%m.%Y")
            start = datetime(d1.year, d1.month, d1.day)
            end = datetime(d2.year, d2.month, d2.day) + timedelta(days=1)
            label = f"{parts[0]} — {parts[1]}"
        else:
            raise ValueError
    except ValueError:
        await message.answer("Формат: 05.10.2026 или 01.10.2026 - 05.10.2026")
        return
    await state.clear()
    await _render(message, db, config, start, end, label, edit=False)
