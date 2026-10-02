"""Автономный клиент LioGames Distribution Hub.

Единственная внешняя зависимость — ``requests`` (и только для реальных сетевых
вызовов; в mock-режиме сеть не нужна вовсе).

Ключевые принципы, продиктованные особенностями API поставщика:

* Подпись запроса — ``HMAC-SHA256`` от ТОЧНОГО сырого JSON-тела.
  Тело сериализуется компактно (``separators=(",", ":")``) с
  ``ensure_ascii=True`` — это повторяет поведение PHP
  ``json_encode($p, JSON_UNESCAPED_SLASHES)``. В сеть уходят РОВНО те же байты,
  которые были подписаны (поэтому используется ``data=body``, а не ``json=``).
* Каталог через API считается ненадёжным (``/products`` отдаёт 500), поэтому
  соответствие «номинал -> variation_id» берётся из карты, переданной в
  конструктор (обычно из переменной окружения ``LIOG_VARIATIONS``).
* Поставщик — ТОЛЬКО источник товара. Приёма денег от покупателей здесь нет.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any, Dict, Mapping, Optional, Union

try:  # requests нужен только для реальных запросов, не для mock-режима
    import requests
except ImportError:  # pragma: no cover - подсказка появляется лишь при live-вызове
    requests = None  # type: ignore[assignment]


DEFAULT_BASE_URL = "https://distribution.liogames.com/api/v1"

# Статусы финального состояния заказа (регистр не важен).
SUCCESS_STATUSES = frozenset(
    {"completed", "complete", "done", "delivered", "success"}
)
FAILURE_STATUSES = frozenset(
    {
        "failed",
        "error",
        "cancelled",
        "canceled",
        "refunded",
        "declined",
        "void",
        "expired",
        "rejected",
    }
)

# Коды из конверта ответа, означающие, что заказ ещё в обработке (не финал).
PROCESSING_CODES = frozenset({"PROCESSING"})


class LioGamesError(Exception):
    """Базовое исключение клиента."""


class LioGamesConfigError(LioGamesError):
    """Некорректная/неполная конфигурация клиента."""


class LioGamesAPIError(LioGamesError):
    """API вернул конверт с ``ok=false`` или HTTP-ошибку.

    Атрибуты
    --------
    code: машинный код ошибки из конверта (например ``INSUFFICIENT_BALANCE``).
    message: человекочитаемое сообщение.
    http_status: HTTP-код ответа (если был).
    body: разобранный конверт/тело ответа.
    """

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        http_status: Optional[int] = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.body = body


def _parse_variations(raw: Union[str, Mapping[str, Any], None]) -> Dict[int, int]:
    """Разобрать карту вариаций «номинал -> variation_id».

    Поддерживаются форматы:

    * dict: ``{60: 534124, "325": 534125}``
    * JSON-строка: ``{"60": 534124, "325": 534125}``
    * компактная строка: ``"60:534124,325:534125"``
    """
    if raw is None or raw == "":
        return {}

    if isinstance(raw, Mapping):
        items = raw.items()
    else:
        text = str(raw).strip()
        if text.startswith("{"):
            items = json.loads(text).items()
        else:
            items = (
                pair.split(":", 1)
                for pair in text.split(",")
                if pair.strip()
            )

    result: Dict[int, int] = {}
    for key, value in items:
        try:
            result[int(str(key).strip())] = int(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise LioGamesConfigError(
                f"Некорректная запись карты вариаций: {key!r} -> {value!r}"
            ) from exc
    return result


class LioGamesClient:
    """Клиент к LioGames Distribution Hub.

    Параметры
    ---------
    member_code:
        Идентификатор аккаунта (не секрет), например ``M2609247OQFNWJJ8W``.
    secret:
        Секрет подписи HMAC. Обязателен для подписанных запросов в live-режиме.
    base_url:
        Базовый URL API.
    product_id:
        ID товара по умолчанию для ``order_create`` (для PUBG — ``66599``).
    variations:
        Карта «номинал -> variation_id» (str/dict/compact — см. _parse_variations).
    key_id:
        Необязательный scoped-ключ ``lk_...``; тогда подпись считается секретом
        этого ключа, а в заголовок добавляется ``X-LIOG-KEY-ID``.
    mock:
        Если True — сеть не используется, ответы генерируются локально.
    timeout:
        Таймаут HTTP-запроса в секундах.
    """

    def __init__(
        self,
        *,
        member_code: str,
        secret: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        product_id: Optional[int] = None,
        variations: Union[str, Mapping[str, Any], None] = None,
        key_id: Optional[str] = None,
        mock: bool = False,
        timeout: float = 30.0,
        session: Any = None,
    ) -> None:
        if not member_code:
            raise LioGamesConfigError("member_code обязателен")

        self.member_code = member_code
        self.secret = secret
        self.base_url = base_url.rstrip("/")
        self.product_id = int(product_id) if product_id else None
        self.variations = _parse_variations(variations)
        self.key_id = key_id
        self.mock = mock
        self.timeout = timeout
        self._session = session

    # ------------------------------------------------------------------ #
    # Конструктор из переменных окружения                                 #
    # ------------------------------------------------------------------ #
    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, **overrides: Any) -> "LioGamesClient":
        """Собрать клиент из переменных окружения (см. .env.example)."""
        env = env if env is not None else os.environ

        def _truthy(value: Optional[str]) -> bool:
            return str(value).strip().lower() in {"1", "true", "yes", "on"}

        kwargs: Dict[str, Any] = {
            "member_code": env.get("LIOG_MEMBER_CODE", ""),
            "secret": env.get("LIOG_SECRET") or None,
            "base_url": env.get("LIOG_BASE_URL", DEFAULT_BASE_URL),
            "product_id": env.get("LIOG_PRODUCT_ID") or None,
            "variations": env.get("LIOG_VARIATIONS") or None,
            "key_id": env.get("LIOG_KEY_ID") or None,
            "mock": _truthy(env.get("LIOG_MOCK")),
        }
        kwargs.update(overrides)
        return cls(**kwargs)

    # ------------------------------------------------------------------ #
    # Подпись                                                             #
    # ------------------------------------------------------------------ #
    @staticmethod
    def serialize_body(payload: Mapping[str, Any]) -> str:
        """Сериализовать тело РОВНО так, как оно будет подписано и отправлено.

        Повторяет PHP ``json_encode($p, JSON_UNESCAPED_SLASHES)``:
        компактно, без экранирования слэшей, с ``\\uXXXX`` для не-ASCII.
        """
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)

    def sign(self, body: str) -> str:
        """Вернуть ``hex`` HMAC-SHA256 от сырого тела запроса."""
        if not self.secret:
            raise LioGamesConfigError(
                "LIOG_SECRET не задан — подпись невозможна (нужен секрет)"
            )
        return hmac.new(
            self.secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # ------------------------------------------------------------------ #
    # Низкоуровневый транспорт                                            #
    # ------------------------------------------------------------------ #
    def _signed_post(self, path: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Отправить подписанный POST и вернуть разобранный конверт ``data``-наружу.

        Возвращает весь конверт ``{"ok","code","message","data"}``.
        Бросает :class:`LioGamesAPIError`, если ``ok`` ложно или HTTP != 2xx.
        """
        body = self.serialize_body(payload)

        if self.mock:
            return self._mock_response(path, payload)

        if requests is None:
            raise LioGamesConfigError(
                "Пакет 'requests' не установлен, а mock-режим выключен"
            )

        signature = self.sign(body)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-liog-sign": signature,
        }
        if self.key_id:
            headers["X-LIOG-KEY-ID"] = self.key_id

        url = f"{self.base_url}{path}"
        http = self._session or requests
        # ВАЖНО: отправляем РОВНО те байты, которые подписали (data=, не json=).
        response = http.post(
            url,
            data=body.encode("utf-8"),
            headers=headers,
            timeout=self.timeout,
        )

        return self._handle_response(response)

    @staticmethod
    def _handle_response(response: Any) -> Dict[str, Any]:
        try:
            envelope = response.json()
        except ValueError:
            raise LioGamesAPIError(
                f"Ответ не является JSON (HTTP {response.status_code})",
                http_status=response.status_code,
                body=getattr(response, "text", None),
            )

        if not isinstance(envelope, dict):
            raise LioGamesAPIError(
                "Неожиданный формат ответа (ожидался объект-конверт)",
                http_status=response.status_code,
                body=envelope,
            )

        ok = bool(envelope.get("ok"))
        code = envelope.get("code")
        message = envelope.get("message") or ""

        if not ok or not (200 <= response.status_code < 300):
            raise LioGamesAPIError(
                message or f"Ошибка LioGames (HTTP {response.status_code})",
                code=code,
                http_status=response.status_code,
                body=envelope,
            )

        return envelope

    # ------------------------------------------------------------------ #
    # Публичные методы API                                               #
    # ------------------------------------------------------------------ #
    def balance(self) -> Dict[str, Any]:
        """POST /balance — кошелёк только на чтение. Возвращает ``data``."""
        envelope = self._signed_post("/balance", {"member_code": self.member_code})
        return envelope.get("data", {})

    def order_create(
        self,
        variation_id: int,
        client_ref: str,
        *,
        product_id: Optional[int] = None,
        user_id: Optional[str] = None,
        server_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /order-create.

        Без поля ``quantity``. Идемпотентность — по ``client_ref`` (при повторе
        отправляйте тот же ``client_ref``). Лимит ~1 заказ / 60 сек на аккаунт.
        """
        pid = int(product_id) if product_id else self.product_id
        if not pid:
            raise LioGamesConfigError(
                "product_id не задан (ни в вызове, ни в конфиге клиента)"
            )
        if not client_ref:
            raise LioGamesConfigError("client_ref обязателен для идемпотентности")

        payload: Dict[str, Any] = {
            "member_code": self.member_code,
            "product_id": int(pid),
            "variation_id": int(variation_id),
            "client_ref": client_ref,
        }
        # Для ваучеров (PUBG 66599) player details не нужны — поля опциональны.
        if user_id is not None:
            payload["user_id"] = user_id
        if server_id is not None:
            payload["server_id"] = server_id

        envelope = self._signed_post("/order-create", payload)
        return envelope.get("data", {})

    def order_status(
        self,
        *,
        order_id: Optional[Union[int, str]] = None,
        client_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /order-status по ``order_id`` ЛИБО ``client_ref``. Возвращает ``data``."""
        if not order_id and not client_ref:
            raise LioGamesConfigError(
                "Нужен либо order_id, либо client_ref для order_status"
            )

        payload: Dict[str, Any] = {"member_code": self.member_code}
        if order_id is not None:
            payload["order_id"] = order_id
        if client_ref is not None:
            payload["client_ref"] = client_ref

        envelope = self._signed_post("/order-status", payload)
        return envelope.get("data", {})

    # ------------------------------------------------------------------ #
    # Разрешение вариаций / разбор статуса                                #
    # ------------------------------------------------------------------ #
    def resolve_variation_id(self, denomination: Union[int, str]) -> int:
        """Вернуть ``variation_id`` по номиналу (напр. 60 -> 534124).

        Опирается на локальную карту (env ``LIOG_VARIATIONS``), а НЕ на сломанный
        каталог ``/products``.
        """
        try:
            denom = int(denomination)
        except (TypeError, ValueError) as exc:
            raise LioGamesConfigError(f"Некорректный номинал: {denomination!r}") from exc

        if denom not in self.variations:
            raise LioGamesConfigError(
                f"Номинал {denom} отсутствует в карте вариаций. "
                f"Доступно: {sorted(self.variations)}"
            )
        return self.variations[denom]

    @staticmethod
    def extract_code(data: Mapping[str, Any]) -> Optional[str]:
        """Достать код выдачи из ``data`` завершённого заказа.

        Источники по приоритету: ``sn`` -> ``delivery_code`` -> ``delivery.codes[0]``.
        Принимает как весь конверт (с ключом ``data``), так и саму ``data``.
        """
        if not isinstance(data, Mapping):
            return None

        # Допускаем передачу целого конверта {"ok":..,"data":{...}}.
        if "data" in data and isinstance(data.get("data"), Mapping):
            data = data["data"]  # type: ignore[assignment]

        sn = data.get("sn")
        if sn:
            return str(sn)

        delivery_code = data.get("delivery_code")
        if delivery_code:
            return str(delivery_code)

        delivery = data.get("delivery")
        if isinstance(delivery, Mapping):
            codes = delivery.get("codes")
            if isinstance(codes, (list, tuple)) and codes:
                return str(codes[0])

        return None

    @staticmethod
    def _status_value(data: Mapping[str, Any]) -> str:
        if isinstance(data, Mapping) and "data" in data and isinstance(data.get("data"), Mapping):
            data = data["data"]  # type: ignore[assignment]
        status = ""
        if isinstance(data, Mapping):
            status = str(data.get("status") or data.get("result") or "")
        return status.strip().lower()

    @classmethod
    def status_is_terminal_ok(cls, data: Mapping[str, Any]) -> bool:
        """True, если заказ успешно завершён (completed/delivered/success/...)."""
        return cls._status_value(data) in SUCCESS_STATUSES

    @classmethod
    def status_is_terminal_failed(cls, data: Mapping[str, Any]) -> bool:
        """True, если заказ завершён неудачей (failed/cancelled/refunded/...)."""
        return cls._status_value(data) in FAILURE_STATUSES

    @classmethod
    def status_is_terminal(cls, data: Mapping[str, Any]) -> bool:
        """True, если заказ в любом финальном состоянии (успех или провал)."""
        return cls.status_is_terminal_ok(data) or cls.status_is_terminal_failed(data)

    # ------------------------------------------------------------------ #
    # Mock-режим (без сети)                                               #
    # ------------------------------------------------------------------ #
    def _mock_response(self, path: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Сгенерировать детерминированный ответ без обращения к сети.

        Нужен для локальной разработки и тестов. Повторяет форму боевых ответов.
        """
        if path == "/balance":
            return {
                "ok": True,
                "code": "BALANCE",
                "message": "OK",
                "data": {"member_code": self.member_code, "balance": 0.0, "currency": "USD"},
            }

        if path == "/order-create":
            client_ref = str(payload.get("client_ref", ""))
            # Детерминированный order_id из client_ref — чтобы повтор был идемпотентным.
            order_id = int(hashlib.sha256(client_ref.encode()).hexdigest()[:8], 16)
            return {
                "ok": True,
                "code": "ORDER_CREATED",
                "message": "OK",
                "data": {
                    "order_id": order_id,
                    "client_ref": client_ref,
                    "status": "processing",
                    "product_id": payload.get("product_id"),
                    "variation_id": payload.get("variation_id"),
                },
            }

        if path == "/order-status":
            ref = str(payload.get("client_ref") or payload.get("order_id") or "mock")
            sn = "MOCK" + hashlib.sha256(ref.encode()).hexdigest()[:14].upper()
            return {
                "ok": True,
                "code": "ORDER_STATUS",
                "message": "OK",
                "data": {
                    "order_id": payload.get("order_id"),
                    "client_ref": payload.get("client_ref"),
                    "status": "completed",
                    "result": "SUCCESS",
                    "is_paid": True,
                    "sn": sn,
                    "delivery_code": sn,
                    "delivery": {"ready": True, "codes": [sn]},
                },
            }

        return {"ok": True, "code": "OK", "message": "mock", "data": {}}
