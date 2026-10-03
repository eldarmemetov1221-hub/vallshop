"""Тексты интерфейса (RU) и форматирование."""

from __future__ import annotations

import os
from decimal import Decimal

_CE = os.getenv("CUSTOM_EMOJI", "1").strip().lower() in {"1", "true", "yes", "on"}


def ce(emoji_id: str, fallback: str = "•") -> str:
    """Премиум-эмодзи через <tg-emoji>, либо обычный fallback (если выключено)."""
    if _CE and emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback


def money(amount: Decimal, currency: str = "USDT") -> str:
    return f"{Decimal(amount):.2f} {currency}"


START = (
    f'{ce("5985478698722136468", "👋")} '
    "Добро пожаловать в <b>VallShop</b> — магазин цифровых товаров.\n\n"
    f'{ce("5879814368572478751", "💳")} '
    "Пополните баланс в USDT и покупайте — товар придёт сюда, в чат."
)

CATALOG_EMPTY = "Каталог пока пуст. Загляните позже 🙌"
CHOOSE_PRODUCT = f'{ce("5805550320985578625", "🛍")} <b>Каталог</b>\nВыберите товар:'
OUT_OF_STOCK_NOTE = "⏳ под заказ"

PAYMENT_CREATED = (
    "🧾 <b>Счёт на оплату</b>\n\n"
    "Товар: <b>{item}</b>\n"
    "К оплате: <b>{amount}</b>\n"
    "Сеть: <b>{network}</b>\n\n"
    "Адрес для перевода USDT:\n<code>{address}</code>\n\n"
    "⚠️ Переведите <b>ровно</b> указанную сумму в сети <b>{network}</b>.\n"
    "После оплаты нажмите «Проверить оплату»."
)

PAYMENT_PENDING = "⏳ Оплата пока не подтверждена. Попробуйте через минуту."
PAYMENT_EXPIRED = "❌ Срок оплаты счёта истёк. Оформите заказ заново."

DELIVERY_SUCCESS = (
    "✅ <b>Оплачено!</b> Ваш код:\n\n"
    "<code>{code}</code>\n\n"
    "Спасибо за покупку в VallShop 💚"
)

FULFILLING = (
    "✅ Оплата получена! Товара сейчас нет в наличии — оформляем у поставщика.\n"
    "Код придёт в этот чат в течение нескольких минут."
)

FULFILL_FAILED = (
    "⚠️ Оплата получена, но выдать код автоматически не удалось.\n"
    "Мы уже разбираемся — админ свяжется с вами. Заказ: <code>{ref}</code>"
)

NOT_ADMIN = "Команда доступна только администратору."

# ── Покупка с баланса ──────────────────────────────────────────────────────
CHOOSE_QUANTITY = (
    "<b>{item}</b>\n"
    "Цена: <b>{price}</b> / шт\n"
    "В наличии: <b>{stock}</b> шт\n"
    "Ваш баланс: <b>{balance}</b>\n\n"
    "Выберите количество:"
)

PURCHASE_SUCCESS = (
    "✅ <b>Покупка оформлена!</b>\n"
    "Товар: <b>{item}</b> × {qty}\n"
    "Списано: <b>{total}</b>\n"
    "Остаток баланса: <b>{balance}</b>\n\n"
    "Ваши коды:\n{codes}\n\n"
    "Спасибо за покупку в VallShop 💚"
)

NOT_ENOUGH_BALANCE = (
    "❌ Недостаточно средств. Нужно {total}, на балансе {balance}.\n"
    "Пополните баланс в профиле."
)
NOT_ENOUGH_STOCK = "❌ Столько нет в наличии. Доступно: {stock} шт."
OUT_OF_STOCK_FULL = "😔 Товара сейчас нет в наличии. Загляните позже."

# ── Профиль / баланс / пополнение ──────────────────────────────────────────
PROFILE = (
    f'{ce("6035084557378654059", "👤")} <b>Мой профиль</b>\n\n'
    "ID: <code>{user_id}</code>\n"
    "Баланс: <b>{balance}</b>\n"
    "Заказов: <b>{orders}</b>"
)

BALANCE_VIEW = (
    f'{ce("5852806433099225341", "🔴")} <b>Мой баланс</b>\n\n'
    "Текущий баланс: <b>{balance}</b>"
)

TOPUP_ASK_AMOUNT = (
    "🟢 <b>Пополнение баланса</b>\n\n"
    "Введите сумму пополнения в USDT (от {min} до {max}):"
)
TOPUP_BAD_AMOUNT = "Введите число от {min} до {max}, например 10"
TOPUP_CHOOSE_NETWORK = (
    "Сумма пополнения: <b>{amount}</b>\n\nВыберите сеть для оплаты USDT:"
)
TOPUP_CREATED = (
    f'{ce("5915758816728718414", "🧾")} <b>Счёт на пополнение</b>\n\n'
    "Зачислим на баланс: <b>{credit}</b>\n"
    "К оплате: <b>{amount}</b>\n"
    "Сеть: <b>{network}</b>\n\n"
    "Адрес для перевода USDT:\n<code>{address}</code>\n\n"
    "⚠️ Переведите <b>ровно</b> указанную сумму в сети <b>{network}</b>.\n"
    "После оплаты нажмите «Проверить оплату»."
)
TOPUP_PENDING = "⏳ Оплата пока не подтверждена. Попробуйте через минуту."
TOPUP_EXPIRED = "❌ Срок оплаты счёта истёк. Создайте пополнение заново."
TOPUP_SUCCESS = (
    "✅ <b>Баланс пополнен!</b>\n"
    "Зачислено: <b>{credit}</b>\n"
    "Текущий баланс: <b>{balance}</b>"
)

NO_ORDERS = "У вас пока нет заказов."
NO_TOPUPS = "У вас пока нет пополнений."
MY_ORDERS_TITLE = "🟡 <b>Мои заказы</b>"
MY_TOPUPS_TITLE = "📜 <b>История пополнений</b>"
