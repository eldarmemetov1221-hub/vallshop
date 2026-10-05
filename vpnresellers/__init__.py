"""Клиент VPNresellers (готовые VPN-серверы, реселлинг)."""

from .client import (
    VPNResellersAPIError,
    VPNResellersClient,
    VPNResellersConfigError,
    VPNResellersError,
    gen_password,
    gen_username,
)

__all__ = [
    "VPNResellersClient",
    "VPNResellersError",
    "VPNResellersConfigError",
    "VPNResellersAPIError",
    "gen_username",
    "gen_password",
]
