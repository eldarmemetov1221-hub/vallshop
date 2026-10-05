"""Клиент VPNresellers API (v4_1) — готовые VPN-серверы под реселлинг.

Док: https://api.vpnresellers.com/docs/v4_1/
База: https://api.vpnresellers.com/v4_1
Авторизация: заголовок ``Authorization: Bearer <token>``.
Биллинг: предоплатный баланс, ежедневное списание за активный аккаунт.

Поддерживается mock-режим (без сети) для тестов.
"""

from __future__ import annotations

import base64
import json as _json
import re
import secrets
from typing import Any, Dict, List, Mapping, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

DEFAULT_BASE_URL = "https://api.vpnresellers.com/v4_1"

_VLESS_RE = re.compile(r"vless://[^\s\"'<>]+")


class VPNResellersError(Exception):
    pass


class VPNResellersConfigError(VPNResellersError):
    pass


class VPNResellersAPIError(VPNResellersError):
    def __init__(self, message, *, http_status=None, body=None):
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.body = body


def gen_username() -> str:
    return "vs" + secrets.token_hex(6)


def gen_password() -> str:
    return secrets.token_urlsafe(12)


class VPNResellersClient:
    def __init__(
        self,
        *,
        token: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        mock: bool = False,
        timeout: float = 30.0,
        session: Any = None,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.mock = mock
        self.timeout = timeout
        self._session = session

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, **overrides: Any) -> "VPNResellersClient":
        import os

        env = env if env is not None else os.environ

        def truthy(v):
            return str(v).strip().lower() in {"1", "true", "yes", "on"}

        kwargs: Dict[str, Any] = {
            "token": env.get("VPNR_TOKEN") or None,
            "base_url": env.get("VPNR_BASE_URL", DEFAULT_BASE_URL),
            "mock": truthy(env.get("VPNR_MOCK")),
        }
        kwargs.update(overrides)
        return cls(**kwargs)

    @property
    def configured(self) -> bool:
        return self.mock or bool(self.token)

    # ── Транспорт ────────────────────────────────────────────────────────────
    def _headers(self, accept: str = "application/json") -> Dict[str, str]:
        if not self.token:
            raise VPNResellersConfigError("VPNR_TOKEN не задан")
        return {
            "Accept": accept,
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping] = None,
        json_body: Optional[Mapping] = None,
        accept: str = "application/json",
        raw: bool = False,
    ):
        if self.mock:
            return self._mock(method, path, params, json_body, raw=raw)
        if requests is None:
            raise VPNResellersConfigError("Пакет 'requests' не установлен, а mock выключен")
        http = self._session or requests
        url = f"{self.base_url}{path}"
        resp = http.request(
            method, url,
            params=params,
            data=_json.dumps(json_body).encode() if json_body is not None else None,
            headers=self._headers(accept),
            timeout=self.timeout,
        )
        if resp.status_code < 200 or resp.status_code >= 300:
            body = None
            try:
                body = resp.json()
            except Exception:  # noqa: BLE001
                body = getattr(resp, "text", None)
            msg = ""
            if isinstance(body, dict):
                msg = body.get("message") or body.get("detail") or body.get("error") or ""
            raise VPNResellersAPIError(
                msg or f"Ошибка VPNresellers (HTTP {resp.status_code})",
                http_status=resp.status_code, body=body,
            )
        if raw:
            return resp.text
        try:
            return resp.json()
        except ValueError:
            return resp.text

    @staticmethod
    def _unwrap(data):
        if isinstance(data, dict) and isinstance(data.get("data"), (dict, list)):
            return data["data"]
        return data

    # ── Профиль / баланс ───────────────────────────────────────────────────────
    def profile(self) -> Dict[str, Any]:
        """GET /profile — содержит баланс реселлера."""
        return self._unwrap(self._request("GET", "/profile"))

    def balance(self) -> Optional[str]:
        try:
            p = self.profile()
            return str(p.get("balance")) if isinstance(p, dict) else None
        except Exception:  # noqa: BLE001
            return None

    # ── Серверы ────────────────────────────────────────────────────────────────
    def list_vless_servers(self) -> List[Dict[str, Any]]:
        d = self._unwrap(self._request("GET", "/vless-servers"))
        return d if isinstance(d, list) else (d.get("servers", []) if isinstance(d, dict) else [])

    # ── Аккаунты ─────────────────────────────────────────────────────────────
    def create_account(
        self, *, username: str, password: str, expire: Optional[str] = None
    ) -> Dict[str, Any]:
        """POST /accounts -> аккаунт. Возвращает нормализованный dict с id/username."""
        body: Dict[str, Any] = {"username": username, "password": password}
        d = self._unwrap(self._request("POST", "/accounts", json_body=body))
        acc = d.get("account", d) if isinstance(d, dict) else {}
        account_id = acc.get("id") or acc.get("account_id") or d.get("id")
        if expire and account_id:
            try:
                self.set_expire(account_id, expire)
            except Exception:  # noqa: BLE001 — срок можно выставить позже вручную
                pass
        return {"id": account_id, "username": acc.get("username", username), "raw": d}

    def set_expire(self, account_id, expire_date: str) -> Any:
        """PUT /accounts/{id}/expire (Y-m-d) или null для авто-продления."""
        return self._request(
            "PUT", f"/accounts/{account_id}/expire", json_body={"expire": expire_date}
        )

    def disable_account(self, account_id) -> Any:
        return self._request("PUT", f"/accounts/{account_id}/disable")

    def delete_account(self, account_id) -> Any:
        return self._request("DELETE", f"/accounts/{account_id}")

    def vless_config(self, *, server_id, account_id) -> Optional[str]:
        """GET /configuration/vless -> извлекаем vless:// ссылку из ответа."""
        data = self._request(
            "GET", "/configuration/vless",
            params={"server_id": server_id, "account_id": account_id},
        )
        return self._extract_vless(data)

    @staticmethod
    def _extract_vless(data) -> Optional[str]:
        if isinstance(data, str):
            m = _VLESS_RE.search(data)
            return m.group(0) if m else None
        if isinstance(data, dict):
            # Частые поля; иначе ищем vless:// в любом строковом значении.
            for key in ("config", "link", "uri", "url", "vless"):
                v = data.get(key)
                if isinstance(v, str) and v.startswith("vless://"):
                    return v
            m = _VLESS_RE.search(_json.dumps(data))
            return m.group(0) if m else None
        return None

    def build_subscription(self, *, account_id, server_ids: List) -> str:
        """Собрать подписку (base64 из списка vless:// по выбранным серверам)."""
        links: List[str] = []
        for sid in server_ids:
            link = self.vless_config(server_id=sid, account_id=account_id)
            if link:
                links.append(link)
        payload = "\n".join(links)
        return base64.b64encode(payload.encode("utf-8")).decode("ascii")

    # ── Mock ───────────────────────────────────────────────────────────────────
    def _mock(self, method, path, params, json_body, raw=False):
        if path == "/profile":
            return {"balance": "25.00", "currency": "USD"}
        if path == "/vless-servers":
            return [
                {"id": 1, "country_code": "NL", "city": "Amsterdam", "status": "online"},
                {"id": 2, "country_code": "DE", "city": "Frankfurt", "status": "online"},
                {"id": 3, "country_code": "US", "city": "New York", "status": "online"},
            ]
        if path == "/accounts" and method == "POST":
            return {"account": {"id": "mock-acc-1", "username": json_body.get("username")}}
        if path.endswith("/expire"):
            return {"ok": True}
        if path == "/configuration/vless":
            sid = (params or {}).get("server_id")
            acc = (params or {}).get("account_id")
            return {"config": f"vless://{acc}@node{sid}.example:443?type=tcp#VallShop-{sid}"}
        if method in ("PUT", "DELETE"):
            return {"ok": True}
        return {}
