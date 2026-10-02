"""Платёжный слой магазина VallShop."""

from .base import Invoice, PaymentProvider, PaymentUpdate
from .boltutil import BoltUtilProvider
from .mock import MockProvider


def build_provider(config) -> PaymentProvider:
    """Собрать провайдера по конфигу."""
    if config.mock_payments or not (config.bolt_api_key and config.bolt_secret):
        return MockProvider(
            network=config.bolt_network,
            expire_minutes=config.payment_timeout_minutes,
        )
    return BoltUtilProvider(
        base_url=config.bolt_base_url,
        api_key=config.bolt_api_key,
        secret=config.bolt_secret,  # Webhook Secret — ключ HMAC для всего
        network=config.bolt_network,
        expire_minutes=config.payment_timeout_minutes,
    )


__all__ = [
    "Invoice",
    "PaymentProvider",
    "PaymentUpdate",
    "BoltUtilProvider",
    "MockProvider",
    "build_provider",
]
