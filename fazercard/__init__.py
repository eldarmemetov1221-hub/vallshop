"""Чистый клиент FazerCards Public API."""

from .client import (
    FazerCardClient,
    FazerCardError,
    FazerCardAPIError,
    FazerCardConfigError,
    SUCCESS_STATUSES,
    FAILURE_STATUSES,
)

__all__ = [
    "FazerCardClient",
    "FazerCardError",
    "FazerCardAPIError",
    "FazerCardConfigError",
    "SUCCESS_STATUSES",
    "FAILURE_STATUSES",
]

__version__ = "0.1.0"
