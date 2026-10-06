"""Автономный клиент FazerCards Public API (reseller).

Док: https://api.fzr.cards/public/docs (OpenAPI /public/docs/openapi.json).

Особенности:
* База: ``https://api.fzr.cards``, эндпоинты под ``/api/v2``.
* Авторизация: заголовок ``X-API-Key: fc_...`` (или ``Authorization: Bearer fc_...``).
* Ответы завёрнуты в ``{"ok": bool, ...}``; баланс/цены — в USD строками.
* Категории с мгновенной выдачей кода: gamekeys (ключи), giftcards (коды).
  Topups (пополнения игр) требуют поля игрока (fields) — тоже поддержаны.
* Идемпотентность заказа: заголовок ``Idempotency-Key`` (любая уникальная строка).

Единственная зависимость — ``requests`` (и только для реальных вызовов; в
mock-режиме сеть не нужна).
"""

from __future__ import annotations

import json as _json
from typing import Any, Dict, List, Mapping, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

DEFAULT_BASE_URL = "https://api.fzr.cards"

SUCCESS_STATUSES = frozenset(
    {"completed", "complete", "done", "delivered", "success", "fulfilled"}
)
# Терминальные «провальные» статусы. Важно: Steam-пополнение и игровые топапы
# по завершении обработки возвращают деньги со статусом ``refund`` (именно так,
# без «-ed» — см. OpenAPI: «processing until completed/refund»). Держим оба
# варианта и синонимы, чтобы поллер гарантированно поймал возврат и вернул
# деньги покупателю, а не завис в FULFILLING навсегда.
FAILURE_STATUSES = frozenset(
    {
        "failed", "error", "cancelled", "canceled",
        "refund", "refunded", "refunding", "returned",
        "reversed", "chargeback", "void", "voided",
        "declined", "rejected",
    }
)


class FazerCardError(Exception):
    pass


class FazerCardConfigError(FazerCardError):
    pass


class FazerCardAPIError(FazerCardError):
    def __init__(self, message, *, code=None, http_status=None, body=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.body = body


class FazerCardClient:
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        mock: bool = False,
        timeout: float = 30.0,
        session: Any = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.mock = mock
        self.timeout = timeout
        self._session = session

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, **overrides: Any) -> "FazerCardClient":
        import os

        env = env if env is not None else os.environ

        def truthy(v):
            return str(v).strip().lower() in {"1", "true", "yes", "on"}

        kwargs: Dict[str, Any] = {
            "api_key": env.get("FZR_API_KEY") or None,
            "base_url": env.get("FZR_BASE_URL", DEFAULT_BASE_URL),
            "mock": truthy(env.get("FZR_MOCK")),
        }
        kwargs.update(overrides)
        return cls(**kwargs)

    # ── Транспорт ────────────────────────────────────────────────────────────
    def _headers(self, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        if not self.api_key:
            raise FazerCardConfigError("FZR_API_KEY не задан")
        h = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
        }
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping] = None,
        json_body: Optional[Mapping] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self.mock:
            return self._mock(method, path, params, json_body)
        if requests is None:
            raise FazerCardConfigError("Пакет 'requests' не установлен, а mock выключен")

        http = self._session or requests
        url = f"{self.base_url}{path}"
        resp = http.request(
            method,
            url,
            params=params,
            data=_json.dumps(json_body).encode() if json_body is not None else None,
            headers=self._headers(idempotency_key),
            timeout=self.timeout,
        )
        return self._handle(resp)

    @staticmethod
    def _handle(resp) -> Dict[str, Any]:
        try:
            data = resp.json()
        except ValueError:
            raise FazerCardAPIError(
                f"Ответ не JSON (HTTP {resp.status_code})",
                http_status=resp.status_code,
                body=getattr(resp, "text", None),
            )
        ok = bool(data.get("ok")) if isinstance(data, dict) else False
        if not ok or not (200 <= resp.status_code < 300):
            msg = ""
            if isinstance(data, dict):
                msg = data.get("error") or data.get("message") or ""
            raise FazerCardAPIError(
                msg or f"Ошибка FazerCard (HTTP {resp.status_code})",
                code=(data.get("code") if isinstance(data, dict) else None),
                http_status=resp.status_code,
                body=data,
            )
        return data

    # ── Аккаунт ──────────────────────────────────────────────────────────────
    def balance(self) -> Dict[str, Any]:
        """GET /balance -> {ok, balance, currency}."""
        return self._request("GET", "/api/v2/balance")

    def me(self) -> Dict[str, Any]:
        return self._request("GET", "/api/v2/me")

    # ── Каталоги ─────────────────────────────────────────────────────────────
    def list_gamekeys(self, *, limit: int = 100, cursor: Optional[str] = None, include_ui: bool = False):
        p = {"limit": limit, "include_ui": 1 if include_ui else 0}
        if cursor:
            p["cursor"] = cursor
        return self._request("GET", "/api/v2/gamekeys", params=p)

    def list_giftcards(self, *, limit: int = 100, cursor: Optional[str] = None, include_ui: bool = False):
        p = {"limit": limit, "include_ui": 1 if include_ui else 0}
        if cursor:
            p["cursor"] = cursor
        return self._request("GET", "/api/v2/giftcards", params=p)

    def list_topups(self, *, limit: int = 100, cursor: Optional[str] = None, include_ui: bool = False):
        p = {"limit": limit, "include_ui": 1 if include_ui else 0}
        if cursor:
            p["cursor"] = cursor
        return self._request("GET", "/api/v2/topups", params=p)

    def list_giftcard_cards(self, *, category_id: str, include_ui: bool = False):
        """GET /giftcards/cards?category_id= -> {ok, offers:[{card_id,name,price_usd,stock,...}]}"""
        p = {"category_id": category_id, "include_ui": 1 if include_ui else 0}
        return self._request("GET", "/api/v2/giftcards/cards", params=p)

    def list_gamekey_keys(self, *, game_id: str, include_ui: bool = False):
        """GET /gamekeys/keys?game_id= -> {ok, keys:[{key_id,name,price_usd,stock,...}]}"""
        p = {"game_id": game_id, "include_ui": 1 if include_ui else 0}
        return self._request("GET", "/api/v2/gamekeys/keys", params=p)

    def list_topup_offers(self, *, category_id: str, include_ui: bool = False):
        """GET /topups/offers?category_id= -> {ok, offers:[...], fields:[...], note}

        Поля игрока (``fields``) задаются на уровне КАТЕГОРИИ, а не оффера:
        каждый — ``{key, label, type: "text"|"select", options?: [...]}``.
        """
        p = {"category_id": category_id, "include_ui": 1 if include_ui else 0}
        return self._request("GET", "/api/v2/topups/offers", params=p)

    def topup_meta(self, category_id: str) -> Dict[str, Any]:
        """Вернуть {fields:[...], note:str} для категории топапов."""
        data = self.list_topup_offers(category_id=category_id)
        fields = data.get("fields") if isinstance(data, dict) else None
        note = data.get("note") if isinstance(data, dict) else None
        return {
            "fields": fields if isinstance(fields, list) else [],
            "note": note or "",
        }

    # ── Агрегаторы каталога (для админ-браузера) ───────────────────────────────
    def all_categories(self, kind: str, *, cap: int = 2000) -> List[Dict[str, Any]]:
        """Собрать все категории/игры раздела (следуя cursor-пагинации).

        kind: "giftcard" | "gamekey" | "topup". Возвращает список
        ``[{"id","name"}, ...]`` (id = category_id / game_id).
        """
        getter = {
            "giftcard": self.list_giftcards,
            "gamekey": self.list_gamekeys,
            "topup": self.list_topups,
        }[kind]
        out: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            data = getter(limit=500, cursor=cursor)
            items = data.get("items") if isinstance(data, dict) else None
            for it in items or []:
                if isinstance(it, Mapping):
                    cid = (
                        it.get("category_id") or it.get("game_id")
                        or it.get("id") or it.get("slug")
                    )
                    out.append({
                        "id": cid,
                        "name": it.get("name") or it.get("title") or cid,
                    })
            meta = (data.get("meta") if isinstance(data, dict) else None) or {}
            cursor = meta.get("next_cursor")
            if not cursor or not meta.get("has_more") or len(out) >= cap:
                break
        return out

    def offers_for(self, kind: str, category_id: str, *, include_ui: bool = False) -> List[Dict[str, Any]]:
        """Получить номиналы (offers/keys) для категории/игры, нормализованно.

        Возвращает ``[{"id","name","price_usd","stock","fields"}, ...]`` где id —
        card_id / key_id / offer_id.
        """
        if kind == "giftcard":
            data = self.list_giftcard_cards(category_id=category_id, include_ui=include_ui)
            rows, id_key = data.get("offers") or [], "card_id"
        elif kind == "gamekey":
            data = self.list_gamekey_keys(game_id=category_id, include_ui=include_ui)
            rows, id_key = data.get("keys") or [], "key_id"
        elif kind == "topup":
            data = self.list_topup_offers(category_id=category_id, include_ui=include_ui)
            rows, id_key = data.get("offers") or [], "offer_id"
        else:
            return []
        out: List[Dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, Mapping):
                continue
            out.append({
                "id": r.get(id_key) or r.get("id") or r.get("card_id")
                      or r.get("key_id") or r.get("offer_id"),
                "name": r.get("name") or r.get("title"),
                "price_usd": r.get("price_usd") or r.get("price"),
                "stock": r.get("stock"),
                "fields": r.get("fields"),
            })
        return out

    # ── Заказы ───────────────────────────────────────────────────────────────
    def order_gamekey(self, *, game_id: str, key_id: str, quantity: int, idempotency_key: Optional[str] = None):
        body = {"game_id": game_id, "key_id": key_id, "quantity": int(quantity)}
        return self._request("POST", "/api/v2/gamekeys/order", json_body=body, idempotency_key=idempotency_key)

    def order_giftcard(self, *, category_id: str, card_id: str, quantity: int, idempotency_key: Optional[str] = None):
        body = {"category_id": category_id, "card_id": card_id, "quantity": int(quantity)}
        return self._request("POST", "/api/v2/giftcards/order", json_body=body, idempotency_key=idempotency_key)

    def order_topup(self, *, category_id: str, offer_id: str, fields: Mapping, idempotency_key: Optional[str] = None):
        body = {"category_id": category_id, "offer_id": offer_id, "fields": dict(fields)}
        return self._request("POST", "/api/v2/topups/order", json_body=body, idempotency_key=idempotency_key)

    def telegram_stars_quote(self) -> Dict[str, Any]:
        """GET /telegram/stars — котировка: price_per_star (USD), min/max количество."""
        return self._request("GET", "/api/v2/telegram/stars")

    def order_telegram_stars(self, *, telegram_username: str, quantity: int, idempotency_key: Optional[str] = None):
        """POST /telegram/stars/buy -> звёзды на username (quantity 50–10000)."""
        body = {"telegram_username": telegram_username, "quantity": int(quantity)}
        return self._request("POST", "/api/v2/telegram/stars/buy", json_body=body, idempotency_key=idempotency_key)

    def order_telegram_premium(self, *, telegram_username: str, months: int, idempotency_key: Optional[str] = None):
        """POST /telegram/premium/buy -> Premium на username (months 3/6/12)."""
        body = {"telegram_username": telegram_username, "months": int(months)}
        return self._request("POST", "/api/v2/telegram/premium/buy", json_body=body, idempotency_key=idempotency_key)

    # ── Пополнение Steam по логину (свободная сумма) ───────────────────────────
    def steam_rates(self) -> Dict[str, Any]:
        """GET /steam-topup/rates — курсы RUB/UAH/KZT к USD и лимиты."""
        return self._request("GET", "/api/v2/steam-topup/rates")

    def steam_check_login(self, steam_login: str) -> Dict[str, Any]:
        """POST /steam-topup/check-login — можно ли пополнить этот логин."""
        return self._request(
            "POST", "/api/v2/steam-topup/check-login",
            json_body={"steamLogin": steam_login},
        )

    def order_steam_topup(
        self, *, steam_login: str, amount, currency: str, idempotency_key: Optional[str] = None
    ):
        """POST /steam-topup/order — пополнить Steam-кошелёк по логину.

        amount — сумма в выбранной валюте (currency: USD|RUB|UAH|KZT).
        """
        body = {
            "steamLogin": steam_login,
            "currency": str(currency).upper(),
            "amount": str(amount),
        }
        return self._request(
            "POST", "/api/v2/steam-topup/order", json_body=body, idempotency_key=idempotency_key
        )

    def get_order(self, order_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/api/v2/orders/{order_id}")

    # ── Разбор выдачи ────────────────────────────────────────────────────────
    @staticmethod
    def extract_codes(order: Mapping[str, Any]) -> List[str]:
        """Достать выданные коды/ключи из объекта заказа.

        Giftcards отдаёт ``cards``, gamekeys — ``keys``; значения бывают
        строками или объектами (ищем code/key/pin/serial/value).
        """
        if isinstance(order, Mapping) and "order" in order and isinstance(order["order"], Mapping):
            order = order["order"]  # допускаем передачу конверта {ok, order}

        codes: List[str] = []

        def take(item: Any) -> None:
            if isinstance(item, str):
                codes.append(item)
            elif isinstance(item, Mapping):
                for k in ("code", "key", "pin", "serial", "value", "voucher", "text"):
                    if item.get(k):
                        codes.append(str(item[k]))
                        return

        for container in ("cards", "keys", "codes", "items", "delivery"):
            val = order.get(container) if isinstance(order, Mapping) else None
            if isinstance(val, list):
                for it in val:
                    take(it)
            elif isinstance(val, Mapping):
                inner = val.get("codes") or val.get("items")
                if isinstance(inner, list):
                    for it in inner:
                        take(it)
            if codes:  # заказ относится к одной категории — хватит первого контейнера
                break
        # Одиночные поля
        if not codes and isinstance(order, Mapping):
            for k in ("code", "key", "voucher"):
                if order.get(k):
                    codes.append(str(order[k]))
        return codes

    @staticmethod
    def _status(order: Mapping[str, Any]) -> str:
        if isinstance(order, Mapping) and "order" in order and isinstance(order["order"], Mapping):
            order = order["order"]
        return str((order or {}).get("status") or "").strip().lower()

    @classmethod
    def status_is_terminal_ok(cls, order: Mapping[str, Any]) -> bool:
        return cls._status(order) in SUCCESS_STATUSES

    @classmethod
    def status_is_terminal_failed(cls, order: Mapping[str, Any]) -> bool:
        return cls._status(order) in FAILURE_STATUSES

    # ── Mock ─────────────────────────────────────────────────────────────────
    def _mock(self, method, path, params, json_body):
        # Steam-пополнение (ветки раньше общего "/order").
        if path.endswith("/steam-topup/rates"):
            return {
                "ok": True,
                "rates": {"USD": "1", "RUB": "83.5", "KZT": "480", "UAH": "41"},
                "limits": {"RUB": {"min": "12.54", "max": "83581.97"}},
            }
        if path.endswith("/steam-topup/check-login"):
            login = (json_body or {}).get("steamLogin") or ""
            return {"ok": True, "valid": bool(login) and login.lower() != "invalid"}
        if path.endswith("/steam-topup/order"):
            return {"ok": True, "order": {"id": "steam-mock", "status": "completed"}}
        if path.endswith("/balance"):
            return {"ok": True, "balance": "100.00", "currency": "USD"}
        if path.endswith("/giftcards/cards"):
            return {"ok": True, "offers": [
                {"card_id": "gc10", "name": "10 USD", "price_usd": "8.50", "stock": 5},
                {"card_id": "gc25", "name": "25 USD", "price_usd": "21.00", "stock": 2},
            ]}
        if path.endswith("/gamekeys/keys"):
            return {"ok": True, "keys": [
                {"key_id": "k1", "name": "Standard Edition", "price_usd": "4.00", "stock": 3},
            ]}
        if path.endswith("/topups/offers"):
            return {
                "ok": True,
                "offers": [
                    {"offer_id": "o1", "name": "60 UC", "price_usd": "1.00"},
                ],
                "fields": [
                    {"key": "player_id", "label": "Player ID", "type": "text"},
                    {"key": "server", "label": "Server", "type": "select",
                     "options": ["Asia", "Europe"]},
                ],
                "note": "Mock topup.",
            }
        if path.endswith(("/giftcards", "/gamekeys", "/topups")):
            return {"ok": True, "items": [
                {"id": "amazon_us", "name": "Amazon (US)"},
                {"id": "steam", "name": "Steam"},
            ], "meta": {"has_more": False, "next_cursor": None}}
        if path.endswith("/telegram/stars"):
            return {
                "ok": True, "kind": "telegram_stars",
                "price_per_star": "0.0152600", "min_amount": 50, "max_amount": 10000,
            }
        if path.endswith("/stars/buy") or path.endswith("/premium/buy"):
            return {"ok": True, "order": {"id": "tg-mock", "status": "completed"}}
        if path.endswith("/order"):
            qty = int((json_body or {}).get("quantity", 1))
            codes = [f"MOCK-{i+1}" for i in range(max(1, qty))]
            return {
                "ok": True,
                "order": {
                    "id": "mock-order",
                    "status": "completed",
                    "cards": codes,
                    "keys": codes,
                },
            }
        if "/orders/" in path:
            return {"ok": True, "order": {"id": path.rsplit("/", 1)[-1], "status": "completed", "cards": ["MOCK-1"]}}
        return {"ok": True, "items": [], "meta": {}}
