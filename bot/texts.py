"""Тексты интерфейса (RU) и форматирование."""

from __future__ import annotations

from decimal import Decimal


def money(amount: Decimal, currency: str = "USDT") -> str:
    return f"{Decimal(amount):.2f} {currency}"


START = (
    "👋 Добро пожаловать в <b>VallShop</b> — магазин цифровых товаров.\n\n"
    "Выберите товар в каталоге, оплатите в USDT — код придёт сюда, в чат."
)

CATALOG_EMPTY = "Каталог пока пуст. Загляните позже 🙌"
CHOOSE_PRODUCT = "🛍 <b>Каталог</b>\nВыберите товар:"
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
