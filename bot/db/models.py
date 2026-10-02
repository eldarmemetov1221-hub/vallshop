"""ORM-модели магазина VallShop.

Структура спроектирована так, чтобы администратор мог сам управлять
каталогом: добавлять товары (Product) и их номиналы (Variant), задавать
наценку/цену и остатки (StockItem), включать/выключать позиции.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


# ──────────────────────────────────────────────────────────────────────────
# Перечисления статусов
# ──────────────────────────────────────────────────────────────────────────
class StockStatus(str, enum.Enum):
    AVAILABLE = "available"   # свободен, можно выдать
    RESERVED = "reserved"     # зарезервирован под заказ
    SOLD = "sold"             # выдан покупателю


class OrderStatus(str, enum.Enum):
    CREATED = "created"               # создан, оплата ещё не начата
    AWAITING_PAYMENT = "awaiting_payment"  # выставлен счёт, ждём оплату
    PAID = "paid"                     # оплата подтверждена
    FULFILLING = "fulfilling"         # идёт выдача (сток/топап)
    COMPLETED = "completed"           # код выдан покупателю
    FAILED = "failed"                 # ошибка выдачи (нужен возврат/ручная работа)
    EXPIRED = "expired"               # счёт просрочен, не оплачен
    REFUNDED = "refunded"             # возврат


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    UNDERPAID = "underpaid"
    EXPIRED = "expired"
    FAILED = "failed"


# ──────────────────────────────────────────────────────────────────────────
# Пользователи
# ──────────────────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # Telegram user id
    username: Mapped[Optional[str]] = mapped_column(String(64))
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    orders: Mapped[List["Order"]] = relationship(back_populates="user")


# ──────────────────────────────────────────────────────────────────────────
# Каталог: товар -> вариации (номиналы)
# ──────────────────────────────────────────────────────────────────────────
class Product(Base):
    """Товар верхнего уровня, например «PUBG Mobile UC (Global)»."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game: Mapped[str] = mapped_column(String(64), default="PUBG")
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    variants: Mapped[List["Variant"]] = relationship(
        back_populates="product",
        order_by="Variant.sort_order",
        cascade="all, delete-orphan",
    )


class Variant(Base):
    """Конкретный номинал товара (например «60 UC»).

    Цена покупателю считается так (см. services.pricing):
      * если задана ``price_usd`` — это фиксированная цена продажи;
      * иначе цена = ``cost_usd`` * (1 + наценка/100), где наценка —
        ``markup_percent`` вариации, а если она не задана — дефолт из конфига.
    """

    __tablename__ = "variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(255))  # «60 UC»

    # Привязка к поставщику LioGames.
    liog_product_id: Mapped[int] = mapped_column(Integer)
    liog_variation_id: Mapped[int] = mapped_column(Integer)

    # Деньги (USD ≈ USDT).
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)   # закупка GOLD
    price_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))   # фикс-цена (переопределение)
    markup_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))  # наценка % (переопределение)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped["Product"] = relationship(back_populates="variants")
    stock_items: Mapped[List["StockItem"]] = relationship(
        back_populates="variant", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("product_id", "liog_variation_id", name="uq_variant_liog"),
    )


# ──────────────────────────────────────────────────────────────────────────
# Сток ваучер-кодов
# ──────────────────────────────────────────────────────────────────────────
class StockItem(Base):
    """Заранее закупленный ваучер-код для мгновенной выдачи."""

    __tablename__ = "stock_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("variants.id", ondelete="CASCADE")
    )
    code: Mapped[str] = mapped_column(String(255))
    status: Mapped[StockStatus] = mapped_column(
        String(16), default=StockStatus.AVAILABLE
    )
    order_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    sold_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    variant: Mapped["Variant"] = relationship(back_populates="stock_items")

    __table_args__ = (
        UniqueConstraint("variant_id", "code", name="uq_stock_variant_code"),
    )


# ──────────────────────────────────────────────────────────────────────────
# Заказы и платежи
# ──────────────────────────────────────────────────────────────────────────
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_ref: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    variant_id: Mapped[int] = mapped_column(ForeignKey("variants.id"))

    price_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[OrderStatus] = mapped_column(String(24), default=OrderStatus.CREATED)

    # Результат выдачи.
    delivery_code: Mapped[Optional[str]] = mapped_column(String(255))
    # Если выдавали через топап у поставщика — его order_id.
    liog_order_id: Mapped[Optional[str]] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="orders")
    variant: Mapped["Variant"] = relationship()
    payment: Mapped[Optional["Payment"]] = relationship(
        back_populates="order", uselist=False, cascade="all, delete-orphan"
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))

    provider: Mapped[str] = mapped_column(String(32), default="boltutil")
    # Идентификатор заказа на стороне платёжного провайдера.
    provider_order_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    checkout_url: Mapped[Optional[str]] = mapped_column(Text)
    address: Mapped[Optional[str]] = mapped_column(String(128))
    network: Mapped[Optional[str]] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    currency: Mapped[str] = mapped_column(String(8), default="USDT")

    status: Mapped[PaymentStatus] = mapped_column(
        String(16), default=PaymentStatus.PENDING
    )
    tx_hash: Mapped[Optional[str]] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    order: Mapped["Order"] = relationship(back_populates="payment")
