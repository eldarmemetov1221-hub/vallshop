# LioGames Distribution Hub — справочник API (источник правды)

> Проверено на боевом аккаунте **VallShop** (уровень **GOLD**).
> Этот документ — единственный источник правды по API поставщика.
> LioGames = **только поставщик товара**. Приём денег от покупателей он **не делает**.

## Базовое

- **Base URL:** `https://distribution.liogames.com/api/v1`
- **Аккаунт:** VallShop, уровень **GOLD**
- **member_code:** `M2609247OQFNWJJ8W` — это идентификатор, **не секрет** (но всё равно храним в `.env`).

## Авторизация и подпись

- Подписанные запросы: **HMAC-SHA256** от **ТОЧНОГО сырого JSON-тела**.
- Заголовок: `x-liog-sign: hash_hmac('sha256', RAW_JSON_BODY, SECRET)`
- Опционально `X-LIOG-KEY-ID: lk_...` для scoped-ключа (тогда подпись секретом этого ключа).
- Тело сериализовать **компактно**, слэши **не экранировать** — как PHP `json_encode($p, JSON_UNESCAPED_SLASHES)`.

### Python (критично!)

```python
import json
body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
# Отправлять РОВНО эти байты и подписывать их же:
#   requests.post(url, data=body.encode(), headers=...)   # data=, НЕ json=payload
```

- `separators=(",", ":")` — компактная сериализация (без пробелов).
- `ensure_ascii=True` — не-ASCII как `\uXXXX` (совпадает с PHP `json_encode`).
- Python `json.dumps` по умолчанию **не** экранирует `/`, так что `JSON_UNESCAPED_SLASHES` уже воспроизведён.
- Подписываем и отправляем **одни и те же байты** — иначе `INVALID_SIGNATURE`.

## Конверт ответа

```json
{"ok": true, "code": "STR", "message": "STR", "data": { }}
```

### Коды ошибок

| Код | Значение |
|---|---|
| `INSUFFICIENT_BALANCE` | Не хватает средств на кошельке |
| `INVALID_SIGNATURE` | Подпись не совпала (чаще всего — несовпадение байтов тела) |
| `NOT_ALLOWED` | Действие недоступно для уровня аккаунта/ключа |
| `PROCESSING` | **Не финал** — опрашивать статус дальше |
| `USERNAME_API_LIMIT_REACHED` | Исчерпан лимит проверок username |

## Эндпоинты

### Служебные
- `GET /ping`
- `GET /routes`

### Каталог (⚠️ ненадёжен)
- `GET /products` — **сейчас отдаёт HTTP 500** (каталог через API сломан у поставщика). **Не завязываться.**
- `GET /products/{id}`
- `GET /products/{id}/variations`
- `GET /product-schema?product_id=&variation_id=`
- `GET /products/{id}/price-matrix` — тоже может отдавать 500.

> Вывод: соответствие «номинал → variation_id» держим в нашей БД/`.env` (`LIOG_VARIATIONS`), а не тянем из `/products`.

### Проверка игрока
- `POST /username-check` `{member_code, game, user_id, server_id}`

### Кошелёк (только чтение)
- `POST /balance` `{member_code}` → `data` с балансом.
- Пополнение: **только вручную** (USDT BEP-20 в панели). **API пополнения нет.**

### Заказы
- `POST /order-create` `{member_code, product_id, variation_id, [user_id, server_id], client_ref}`
  - **Без поля `quantity`!**
  - Лимит **~60 сек между заказами** на аккаунт.
  - Идемпотентность по `client_ref` — при повторе слать **тот же** `client_ref`.
- `POST /order-status` `{member_code, order_id | client_ref}`
- `POST /bulk-orders` — только H2H-уровень, только гифткарты/ваучеры. **Нам недоступно** (у нас GOLD).

### Уведомления / вебхуки
- `GET/POST /notifications/subscriptions`
- Вебхуки: callback URL задаётся **в панели**, на вызов отвечать **2xx**.

### Sandbox
- `POST /sandbox/order-create`
- `GET/POST /sandbox/order-status`
- Sandbox **не выдаёт реальные коды** — только проверка подписи / ID / валидации.

### Agent
- `GET /agent/tools`
- `POST /agent/execute`

### Scopes ключа
`products:read`, `pricing:read`, `username:check`, `orders:create`, `orders:read`, `wallet:read`, `notifications:manage`, `sandbox`.

## Товар: PUBG Mobile Code (Global)

- **product_id:** `66599`
- Это **ВАУЧЕР**: `required_fields` пустые, player details не нужны — выдаётся код.

### Вариации (номинал UC → variation_id, цена GOLD)

| UC | variation_id | Цена (GOLD) |
|---:|---:|---:|
| 60 | 534124 | $0.88 |
| 325 | 534125 | $4.44 |
| 660 | 534126 | $8.89 |
| 1800 | 534127 | $22.24 |
| 3850 | 534128 | $44.49 |
| 8100 | 534129 | $88.99 |
| 16200 | 534130 | $177.99 |
| 24300 | 534131 | $266.99 |
| 32400 | 534132 | $355.99 |
| 40500 | 534133 | $444.99 |

> Всего у товара 16 опций; выше — базовые 10.

## Формат завершённого `order-status` (где лежит код — проверено)

```json
{
  "ok": true,
  "code": "ORDER_STATUS",
  "data": {
    "order_id": 536857,
    "order_number": "575551",
    "status": "completed",
    "result": "SUCCESS",
    "is_paid": true,
    "total": 0.88,
    "items": [
      {"product_id": 66599, "variation_id": 534124, "name": "PUBG Mobile Code (Global) - 60 UC", "qty": 1}
    ],
    "sn": "aYVQtqZs2E27YdH38d",
    "delivery_code": "aYVQtqZs2E27YdH38d",
    "delivery": {"ready": true, "codes": ["aYVQtqZs2E27YdH38d"]}
  }
}
```

**Код выдачи** берётся из `data.sn` **ИЛИ** `data.delivery_code` **ИЛИ** `data.delivery.codes[]`.

- **Статусы успеха:** `completed`, `complete`, `done`, `delivered`, `success`.
- **Статусы провала:** `failed`, `error`, `cancelled`, `refunded`, `declined`, `void`, `expired` и т.п.

## Ключевые ограничения для архитектуры магазина

- LioGames = **только поставщик**. Деньги от покупателей он не принимает — нужен **свой платёжный слой** (CryptoBot / Telegram Stars / ЮKassa / своя крипта).
- Лимит **~1 `order-create` / 60 сек** на аккаунт. Поэтому выгоднее **заранее закупать ваучер-коды в сток** и выдавать мгновенно из своей базы; топап-под-заказ — медленный **фолбэк**.
- Пополнение кошелька поставщика — вручную (USDT BEP-20), API для этого нет.
