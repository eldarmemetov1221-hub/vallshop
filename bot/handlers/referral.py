"""Реферальная программа (клиентская часть): экран «Заработок» и «Реферальная система»."""

from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from ..config import BotConfig
from ..db import Database
from ..services import referral as referral_service
from ..services import settings as settings_service
from ..ui import render
from .. import keyboards as kb
from .. import texts

router = Router(name="referral")


@router.callback_query(F.data == "earn")
async def cb_earn(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render(call, banner="main", caption=texts.EARN_TITLE, reply_markup=kb.earn_kb())
    await call.answer()


async def _referral_screen(call: CallbackQuery, db: Database, config: BotConfig) -> None:
    async with db.session() as session:
        enabled = await settings_service.get_bool(
            session, settings_service.REF_ENABLED, True
        )
        if not enabled:
            await render(call, banner="main", caption=texts.REFERRAL_DISABLED,
                         reply_markup=kb.earn_kb())
            return
        st = await referral_service.stats(session, call.from_user.id)
        terms = await referral_service.terms_text(session)
        min_raw = await settings_service.get(session, settings_service.REF_MIN_WD_USDT)
        min_usdt = Decimal(min_raw) if min_raw else referral_service.DEFAULT_MIN_USDT

    me = await call.message.bot.get_me()
    link = referral_service.ref_link(me.username, call.from_user.id)

    lines = [
        "👥 <b>Реферальная система</b>",
        "",
        terms,
        "",
        "🔗 Ваша ссылка:",
        f"<code>{link}</code>",
        "",
        f"Приглашено: <b>{st['invited']}</b>",
        f"Из них с покупками: <b>{st['with_purchase']}</b>",
        "",
        f"💵 Реферальный баланс: <b>{texts.money(st['bal_usdt'])}</b>",
        f"Заработано всего: <b>{texts.money(st['earned_usdt'])}</b>",
    ]
    # Рублёвые строки показываем только когда там что-то есть (этап с ₽).
    if st["bal_rub"] > 0 or st["earned_rub"] > 0:
        lines.append(f"💴 Реф. баланс ₽: <b>{st['bal_rub']} ₽</b> (заработано {st['earned_rub']} ₽)")

    can_transfer = st["bal_usdt"] >= min_usdt and st["bal_usdt"] > 0
    if not can_transfer:
        lines.append("")
        lines.append(f"ℹ️ Перевод на основной баланс — от {texts.money(min_usdt)}.")

    await render(
        call, banner="main", caption="\n".join(lines),
        reply_markup=kb.referral_kb(can_transfer),
    )


@router.callback_query(F.data == "ref")
async def cb_ref(call: CallbackQuery, db: Database, config: BotConfig, state: FSMContext) -> None:
    await state.clear()
    await _referral_screen(call, db, config)
    await call.answer()


@router.callback_query(F.data == "ref_transfer")
async def cb_ref_transfer(call: CallbackQuery, db: Database, config: BotConfig) -> None:
    async with db.session() as session:
        ok, amount, need = await referral_service.transfer_to_balance(
            session, call.from_user.id, "USDT"
        )
        await session.commit()
    if not ok:
        await call.answer(
            f"Недостаточно для перевода. Минимум {texts.money(need)}.", show_alert=True
        )
        return
    await call.answer(f"Переведено {texts.money(amount)} на основной баланс ✅", show_alert=True)
    await _referral_screen(call, db, config)
