"""Конфигурация бота VallShop из переменных окружения.

Все секреты читаются только из окружения / .env (см. .env.example).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional


def _get(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Обязательная переменная окружения не задана: {name}")
    return value


def _int_list(raw: Optional[str]) -> List[int]:
    if not raw:
        return []
    out: List[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return out


@dataclass
class BotConfig:
    """Настройки Telegram-бота и магазина."""

    bot_token: str
    admin_ids: List[int] = field(default_factory=list)

    # База данных: SQLAlchemy DSN. По умолчанию локальный SQLite-файл.
    database_url: str = "sqlite+aiosqlite:///vallshop.db"

    # Валюта отображения цен (внутренне всё в USD ≈ USDT).
    currency: str = "USDT"

    # Наценка по умолчанию (в процентах) поверх закупочной цены LioGames,
    # если у конкретной вариации не задана своя цена/наценка.
    default_markup_percent: Decimal = Decimal("0")

    # Платёжный провайдер.
    bolt_base_url: str = "https://boltutil.com"
    bolt_api_key: Optional[str] = None
    bolt_secret: Optional[str] = None
    bolt_webhook_secret: Optional[str] = None
    bolt_network: str = "TRC20"
    # Публичный URL сервиса — для callback (notifyUrl) и редиректа после оплаты.
    public_base_url: Optional[str] = None
    payment_timeout_minutes: int = 30

    # Mock-режим платежей/поставщика (без сети) — для локальной разработки.
    mock_payments: bool = False

    @property
    def notify_url(self) -> Optional[str]:
        if not self.public_base_url:
            return None
        return self.public_base_url.rstrip("/") + "/bolt/webhook"

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    @classmethod
    def from_env(cls) -> "BotConfig":
        def _truthy(v: Optional[str]) -> bool:
            return str(v).strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            bot_token=_require("BOT_TOKEN"),
            admin_ids=_int_list(_get("ADMIN_IDS")),
            database_url=_get("DATABASE_URL", "sqlite+aiosqlite:///vallshop.db"),
            currency=_get("SHOP_CURRENCY", "USDT"),
            default_markup_percent=Decimal(_get("DEFAULT_MARKUP_PERCENT", "0")),
            bolt_base_url=_get("BOLT_BASE_URL", "https://boltutil.com"),
            bolt_api_key=_get("BOLT_API_KEY"),
            bolt_secret=_get("BOLT_SECRET"),
            bolt_webhook_secret=_get("BOLT_WEBHOOK_SECRET"),
            bolt_network=_get("BOLT_NETWORK", "TRC20"),
            public_base_url=_get("PUBLIC_BASE_URL"),
            payment_timeout_minutes=int(_get("PAYMENT_TIMEOUT_MINUTES", "30")),
            mock_payments=_truthy(_get("MOCK_PAYMENTS")),
        )
