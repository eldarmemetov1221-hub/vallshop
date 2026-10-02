"""Интерфейс платёжного провайдера.

Вся специфика конкретного провайдера (BoltUtil и т.д.) скрыта за этим
интерфейсом — остальной код магазина от неё не зависит.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Optional


@dataclass
class Invoice:
    """Результат выставления счёта."""

    provider_order_id: str
    checkout_url: Optional[str] = None
    address: Optional[str] = None
    network: Optional[str] = None
    amount: Decimal = Decimal("0")
    currency: str = "USDT"
    expires_at: Optional[datetime] = None
    raw: Optional[Mapping] = None


@dataclass
class PaymentUpdate:
    """Нормализованное состояние платежа (из статуса или вебхука)."""

    provider_order_id: Optional[str]
    status: str  # pending | paid | underpaid | expired | failed
    client_ref: Optional[str] = None
    tx_hash: Optional[str] = None
    amount: Optional[Decimal] = None
    raw: Optional[Mapping] = None


class PaymentProvider(abc.ABC):
    """Абстрактный платёжный провайдер."""

    name: str = "base"

    @abc.abstractmethod
    async def create_invoice(
        self,
        *,
        amount: Decimal,
        client_ref: str,
        description: str,
        notify_url: Optional[str] = None,
        success_url: Optional[str] = None,
        network: Optional[str] = None,
    ) -> Invoice:
        ...

    @abc.abstractmethod
    async def get_status(self, ref: str) -> PaymentUpdate:
        """Статус платежа по ссылке заказа (для BoltUtil — externalOrderId)."""
        ...

    @abc.abstractmethod
    def verify_webhook(self, raw_body: bytes, headers: Mapping[str, str]) -> bool:
        """Проверить подпись вебхука. Никогда не выдавать товар без True."""
        ...

    @abc.abstractmethod
    def parse_webhook(self, raw_body: bytes) -> PaymentUpdate:
        ...
