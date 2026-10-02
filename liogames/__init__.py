"""Чистый клиент LioGames Distribution Hub.

Публичный API пакета — см. liogames.client.
"""

from .client import (
    LioGamesClient,
    LioGamesError,
    LioGamesAPIError,
    LioGamesConfigError,
    SUCCESS_STATUSES,
    FAILURE_STATUSES,
)

__all__ = [
    "LioGamesClient",
    "LioGamesError",
    "LioGamesAPIError",
    "LioGamesConfigError",
    "SUCCESS_STATUSES",
    "FAILURE_STATUSES",
]

__version__ = "0.1.0"
