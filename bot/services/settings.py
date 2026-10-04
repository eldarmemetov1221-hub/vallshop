"""Глобальные настройки магазина (таблица settings, ключ-значение).

Используются для раздела «Текущие заказы»: переключатель автовозврата и
редактируемые тексты, отправляемые покупателю при возврате/отмене.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Setting

# Ключи настроек.
AUTO_REFUND = "orders_auto_refund"   # "1"/"0" — автовозврат денег при провале
REFUND_TEXT = "orders_refund_text"   # текст покупателю при автовозврате
CANCEL_TEXT = "orders_cancel_text"   # текст покупателю при ручной отмене

# Курс USDT→₽ (рубли за 1 USDT). Задаётся администратором.
USDT_RATE = "usdt_rate"

# Реферальная программа
REF_ENABLED = "ref_enabled"              # "1"/"0"
REF_PERCENT = "ref_percent"              # процент с покупок, напр. "5"
REF_MIN_WD_USDT = "ref_min_withdraw_usdt"  # минимум перевода, USDT
REF_MIN_WD_RUB = "ref_min_withdraw_rub"    # минимум перевода, RUB
REF_TERMS = "ref_terms_text"             # текст условий


async def get(session: AsyncSession, key: str, default: Optional[str] = None) -> Optional[str]:
    row = await session.get(Setting, key)
    return row.value if row is not None and row.value is not None else default


async def set(session: AsyncSession, key: str, value: Optional[str]) -> None:
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    await session.flush()


async def get_bool(session: AsyncSession, key: str, default: bool = True) -> bool:
    v = await get(session, key)
    if v is None:
        return default
    return v.strip() in ("1", "true", "yes", "on")


def fmt(template: str, *, ref: str = "", amount: str = "") -> str:
    """Безопасная подстановка {ref}/{amount} без падения на прочих скобках."""
    return template.replace("{ref}", ref).replace("{amount}", amount)
