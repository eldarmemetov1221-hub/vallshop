"""Админ-раздел «Оформление»: тексты, эмодзи, порядок и видимость кнопок меню."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..config import BotConfig
from ..db import Database
from ..services import menu as menu_service

router = Router(name="admin_appearance")


class IsAdminCb(BaseFilter):
    async def __call__(self, call: CallbackQuery, config: BotConfig) -> bool:
        return bool(call.from_user and config.is_admin(call.from_user.id))


class IsAdminMsg(BaseFilter):
    async def __call__(self, message: Message, config: BotConfig) -> bool:
        return bool(message.from_user and config.is_admin(message.from_user.id))


router.callback_query.filter(IsAdminCb())
router.message.filter(IsAdminMsg())


class APState(StatesGroup):
    set_label = State()
    set_emoji = State()
    set_caption = State()


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _cancel(back: str) -> InlineKeyboardMarkup:
    return InlineKeyboardBuilder().row(_btn("⬅️ Отмена", back)).as_markup()


# ── Главный экран «Оформление» ───────────────────────────────────────────────
@router.callback_query(F.data == "ap_home")
async def cb_home(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    kb = InlineKeyboardBuilder()
    for m, title in menu_service.MENU_TITLES.items():
        kb.row(_btn(f"📋 {title}", f"ap_menu:{m}"))
    kb.row(_btn("♻️ Сбросить к стандартному", "ap_reset"))
    kb.row(_btn("⬅️ Назад", "a_home"))
    await call.message.edit_text(
        "🎨 <b>Оформление</b>\n\n"
        "Настройка клиентских кнопок: текст, премиум-эмодзи, порядок и "
        "видимость (вкл/выкл — например, скрыть на время обновления).\n\n"
        "Выберите меню:",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


@router.callback_query(F.data == "ap_reset")
async def cb_reset_ask(call: CallbackQuery) -> None:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("♻️ Да, сбросить", "ap_reset_ok"))
    kb.row(_btn("⬅️ Отмена", "ap_home"))
    await call.message.edit_text(
        "♻️ Сбросить <b>всё оформление</b> к стандартному?\n"
        "Тексты кнопок, эмодзи, порядок и тексты экранов вернутся к значениям "
        "по умолчанию. Скрытые кнопки снова станут видимыми.",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


@router.callback_query(F.data == "ap_reset_ok")
async def cb_reset(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    await menu_service.reset_all(db)
    await state.clear()
    await cb_home(call, state)
    await call.answer("Сброшено ✅")


@router.callback_query(F.data.startswith("ap_menu:"))
async def cb_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    menu = call.data.split(":", 1)[1]
    await _show_menu(call, menu)
    await call.answer()


async def _show_menu(call: CallbackQuery, menu: str) -> None:
    kb = InlineKeyboardBuilder()
    for r in menu_service.menu_entries(menu, only_enabled=False):
        mark = "🟢" if r.enabled else "🔴"
        kb.row(_btn(f"{mark} {r.text}", f"ap_btn:{r.key}"))
    if menu in menu_service.MENU_CAPTION:
        kb.row(_btn("📝 Текст экрана", f"ap_cap:{menu}"))
    kb.row(_btn("⬅️ Назад", "ap_home"))
    title = menu_service.MENU_TITLES.get(menu, menu)
    await call.message.edit_text(
        f"🎨 <b>{title}</b>\nВыберите кнопку для настройки "
        "(🟢 — видна клиентам, 🔴 — скрыта):",
        reply_markup=kb.as_markup(),
    )


async def _show_button(call: CallbackQuery, key: str) -> None:
    r = menu_service.resolved(key)
    e = menu_service.get_entry(key)
    if not r or not e:
        await call.answer("Не найдено", show_alert=True)
        return
    emoji_state = "🙂 есть" if r.emoji_id else "— нет"
    vis = "🟢 видна" if r.enabled else "🔴 скрыта"
    caption = (
        f"🔧 <b>Кнопка</b>\n"
        f"Меню: {menu_service.MENU_TITLES.get(e.menu, e.menu)}\n"
        f"Текст: <b>{r.text}</b>\n"
        f"Эмодзи: {emoji_state}\n"
        f"Видимость: {vis}"
    )
    kb = InlineKeyboardBuilder()
    kb.row(
        _btn("✏️ Текст", f"ap_lbl:{key}"),
        _btn("🙂 Эмодзи", f"ap_emo:{key}"),
    )
    kb.row(
        _btn("🔼 Вверх", f"ap_up:{key}"),
        _btn("🔽 Вниз", f"ap_dn:{key}"),
    )
    tog = "🔴 Скрыть" if r.enabled else "🟢 Показать"
    kb.row(_btn(tog, f"ap_tog:{key}"))
    kb.row(_btn("⬅️ Назад", f"ap_menu:{e.menu}"))
    await call.message.edit_text(caption, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("ap_btn:"))
async def cb_button(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_button(call, call.data.split(":", 1)[1])
    await call.answer()


@router.callback_query(F.data.startswith("ap_tog:"))
async def cb_toggle(call: CallbackQuery, db: Database) -> None:
    key = call.data.split(":", 1)[1]
    await menu_service.toggle(db, key)
    await _show_button(call, key)
    await call.answer("Готово")


@router.callback_query(F.data.startswith("ap_up:"))
async def cb_up(call: CallbackQuery, db: Database) -> None:
    key = call.data.split(":", 1)[1]
    await menu_service.move(db, key, -1)
    await _show_button(call, key)
    await call.answer("⬆️")


@router.callback_query(F.data.startswith("ap_dn:"))
async def cb_down(call: CallbackQuery, db: Database) -> None:
    key = call.data.split(":", 1)[1]
    await menu_service.move(db, key, +1)
    await _show_button(call, key)
    await call.answer("⬇️")


# ── Текст кнопки ─────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("ap_lbl:"))
async def cb_label_ask(call: CallbackQuery, state: FSMContext) -> None:
    key = call.data.split(":", 1)[1]
    r = menu_service.resolved(key)
    await state.set_state(APState.set_label)
    await state.update_data(key=key)
    await call.message.edit_text(
        f"✏️ Текущий текст: <b>{r.text if r else ''}</b>\n\n"
        "Пришлите новый текст кнопки.\n<code>0</code> — вернуть по умолчанию.",
        reply_markup=_cancel(f"ap_btn:{key}"),
    )
    await call.answer()


@router.message(APState.set_label)
async def msg_label(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("key")
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Пусто. Пришлите текст или 0.")
        return
    await menu_service.set_label(db, key, None if raw == "0" else raw[:255])
    await state.clear()
    kb = InlineKeyboardBuilder().row(_btn("⬅️ К кнопке", f"ap_btn:{key}")).as_markup()
    await message.answer("✅ Текст кнопки обновлён", reply_markup=kb)


# ── Эмодзи кнопки ────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("ap_emo:"))
async def cb_emoji_ask(call: CallbackQuery, state: FSMContext) -> None:
    key = call.data.split(":", 1)[1]
    await state.set_state(APState.set_emoji)
    await state.update_data(key=key)
    await call.message.edit_text(
        "🙂 Пришлите <b>премиум-эмодзи</b> (одним символом) — оно станет иконкой кнопки.\n"
        "<code>-</code> — убрать эмодзи.\n"
        "<code>0</code> — вернуть по умолчанию.",
        reply_markup=_cancel(f"ap_btn:{key}"),
    )
    await call.answer()


@router.message(APState.set_emoji)
async def msg_emoji(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("key")
    raw = (message.text or "").strip()
    emoji_id: str | None
    if raw == "0":
        emoji_id = None  # сброс к дефолту
    elif raw == "-":
        emoji_id = ""     # без эмодзи
    else:
        cid = None
        for ent in (message.entities or []):
            if ent.type == "custom_emoji" and getattr(ent, "custom_emoji_id", None):
                cid = ent.custom_emoji_id
                break
        if not cid:
            await message.answer(
                "Не нашёл премиум-эмодзи в сообщении. Пришлите именно премиум-эмодзи, "
                "либо <code>-</code> (убрать) / <code>0</code> (по умолчанию)."
            )
            return
        emoji_id = cid
    await menu_service.set_emoji(db, key, emoji_id)
    await state.clear()
    kb = InlineKeyboardBuilder().row(_btn("⬅️ К кнопке", f"ap_btn:{key}")).as_markup()
    await message.answer("✅ Эмодзи кнопки обновлено", reply_markup=kb)


# ── Текст экрана (приветствие / FAQ) ─────────────────────────────────────────
@router.callback_query(F.data.startswith("ap_cap:"))
async def cb_caption_ask(call: CallbackQuery, state: FSMContext) -> None:
    menu = call.data.split(":", 1)[1]
    await state.set_state(APState.set_caption)
    await state.update_data(menu=menu)
    await call.message.edit_text(
        "📝 Пришлите новый <b>текст экрана</b> (можно премиум-эмодзи и форматирование).\n"
        "<code>0</code> — вернуть текст по умолчанию.",
        reply_markup=_cancel(f"ap_menu:{menu}"),
    )
    await call.answer()


@router.message(APState.set_caption)
async def msg_caption(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    menu = data.get("menu")
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Пусто. Пришлите текст или 0.")
        return
    value = None if raw == "0" else (message.html_text or message.text).strip()
    await menu_service.set_caption(db, menu, value)
    await state.clear()
    kb = InlineKeyboardBuilder().row(_btn("⬅️ Назад", f"ap_menu:{menu}")).as_markup()
    await message.answer("✅ Текст экрана обновлён", reply_markup=kb)
