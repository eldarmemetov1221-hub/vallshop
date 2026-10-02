# VallShop — магазин цифровых товаров (Telegram-бот)

Магазин цифровых товаров поверх поставщика **LioGames Distribution Hub**.
Старт — **PUBG Mobile UC** (ваучер-коды), затем другие игры.

- **Платформа:** Telegram-бот (aiogram 3).
- **Оплата:** **BoltUtil** — некастодиальный USDT-шлюз (TRC20/ERC20/BEP20/Polygon/Solana),
  деньги приходят напрямую на твой кошелёк.
- **Каталог:** полностью управляется админом из бота — товары, номиналы,
  наценка/цена, остатки, видимость.
- **Выдача:** мгновенно из собственного стока кодов; если стока нет — фолбэк
  через заказ у поставщика (топап) с автоопросом статуса.

> **Статус:** рабочий каркас. Полный путь (каталог → заказ → счёт USDT →
> оплата → выдача кода) работает в **mock-режиме** без сети. Интеграция BoltUtil
> реализована по официальной документации (подпись покрыта тестами); для боя
> нужны реальные ключи, публичный HTTPS для вебхука и закупка кодов в сток.

## Архитектура

```
Покупатель ──▶ Telegram-бот (aiogram)
                 │
                 ├─ Каталог/цены ── services/catalog, services/pricing
                 ├─ Заказ ───────── services/orders
                 ├─ Оплата ──────── payments/ (BoltUtil | Mock)  ◀── вебхук/кнопка
                 └─ Выдача ──────── сток (StockItem) ──┐
                                     └ фолбэк: LioGames order_create ─▶ poller
                 │
                 ▼
              БД (SQLAlchemy): users, products, variants, stock_items, orders, payments
```

Поток покупки:
1. Покупатель выбирает товар → номинал. Цена = наценка над закупкой (или фикс-цена).
2. Создаётся заказ + счёт в BoltUtil (адрес USDT + сумма + `checkoutUrl`).
3. Оплата подтверждается **вебхуком** BoltUtil (`/bolt/webhook`, HMAC) либо
   кнопкой «Проверить оплату» (polling статуса).
4. Выдача: берём код из стока (мгновенно) → иначе топап у LioGames →
   фоновый поллер дотягивает `PROCESSING` до готового кода и шлёт его в чат.

## Структура

```
vallshop/
├── liogames/              # клиент поставщика (HMAC, заказы, extract_code, mock)
├── bot/
│   ├── config.py          # настройки из окружения
│   ├── main.py            # точка входа: бот + поллер + (опц.) вебхук-сервер
│   ├── texts.py           # тексты RU
│   ├── keyboards.py       # inline-клавиатуры
│   ├── web.py             # aiohttp-сервер вебхуков BoltUtil
│   ├── db/                # Database, модели (User/Product/Variant/StockItem/Order/Payment)
│   ├── payments/          # интерфейс + BoltUtilProvider + MockProvider
│   ├── services/          # pricing, catalog, stock, orders (выдача), poller
│   └── handlers/          # user (каталог/покупка) + admin (управление)
├── scripts/seed_pubg.py   # наполнить каталог PUBG (идемпотентно)
├── deploy/vallshop-bot.service   # systemd-юнит
├── Dockerfile, docker-compose.yml
├── LIOGAMES_API.md        # справочник API поставщика (источник правды)
├── .env.example, requirements.txt
└── tests/
```

## Запуск (локально, mock-режим)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# заполни BOT_TOKEN и ADMIN_IDS; для старта без крипты/сети: MOCK_PAYMENTS=1, LIOG_MOCK=1

set -a; source .env; set +a
python -m scripts.seed_pubg      # создать товар PUBG + 10 номиналов
python -m bot.main               # запустить бота
```

В mock-режиме «Проверить оплату» сразу считает счёт оплаченным и выдаёт код из стока
(добавь коды через `/addstock`), а при пустом стоке — имитирует топап.

## Админ-команды (для ADMIN_IDS)

| Команда | Назначение |
|---|---|
| `/admin` | меню и статистика |
| `/addproduct game \| Название [\| описание]` | добавить товар |
| `/products` | список товаров |
| `/addvariant product_id \| Название \| liog_product_id \| liog_variation_id \| cost_usd [\| price_usd]` | добавить номинал |
| `/variants product_id` | номиналы товара (цена, маржа, сток) |
| `/setprice variant_id значение\|-` | фикс-цена продажи (`-` — убрать) |
| `/setmarkup variant_id процент\|-` | наценка % для номинала (`-` — дефолт) |
| `/toggleproduct id`, `/togglevariant id` | вкл/выкл видимость |
| `/addstock variant_id` | добавить коды в сток (далее прислать коды построчно) |
| `/stock` | остатки по номиналам |
| `/orders` | последние заказы |

**Ценообразование:** если у номинала задана `price_usd` — это цена продажи;
иначе `cost_usd × (1 + наценка/100)`, где наценка берётся из номинала, а если
не задана — из `DEFAULT_MARKUP_PERCENT`.

## Деплой на сервер

**Docker (рекомендуется):**
```bash
cp .env.example .env   # заполнить
docker compose up -d --build
docker compose exec bot python -m scripts.seed_pubg
```
SQLite-файл лежит в `./data` (volume) — сток и заказы переживают пересборку.

**systemd:** положи проект в `/opt/vallshop`, создай venv `.venv`, заполни `.env`,
скопируй `deploy/vallshop-bot.service` в `/etc/systemd/system/`, затем
`systemctl enable --now vallshop-bot`.

**Вебхуки BoltUtil:** нужен публичный HTTPS. Задай `PUBLIC_BASE_URL`
(например, через reverse-proxy/Caddy/Nginx на `:8080`) — бот поднимет
`POST {PUBLIC_BASE_URL}/bolt/webhook`, этот адрес укажи как notifyUrl в кабинете
BoltUtil. Без публичного URL оплата подтверждается кнопкой «Проверить оплату».

## Платёж BoltUtil (реализовано по офиц. документации)

- **Подпись:** HMAC-SHA256, ключ = **Webhook Secret** (один для запросов и
  колбэков); подписывается строка `"{timestamp_ms}.{raw_body}"`, результат —
  **hex в нижнем регистре**. Покрыто тестами (`tests/test_boltutil.py`).
- **Заголовки запроса:** `X-Bolt-Key`, `X-Bolt-Timestamp`, `X-Bolt-Signature`.
  **Вебхука:** `X-Bolt-Webhook-Timestamp`, `X-Bolt-Webhook-Signature`.
- **Создание:** `POST /api/v1/order/create` (`amount`, `currency=USDT`, `network`,
  `externalOrderId`, `orderDesc`, `notifyUrl`, `returnUrl`, `expiredMinutes`).
  Покупателю показывается **`payment.amount` из ответа** (может быть чуть выше —
  для автосверки; не округляем).
- **Статус:** `POST /api/v1/order/status` по `externalOrderId`. Статусы:
  `PENDING / CONFIRMED / EXPIRED` (успех = `CONFIRMED`).
- **Вебхук:** на валидную подпись отвечаем ровно `{"status":"SUCCESS"}`,
  обработка идемпотентна.

Нюансы BoltUtil: **минимальная сумма 1.00 USDT** (цена номинала должна быть
≥ 1 USDT), **песочницы нет** — для отладки сделай микро-заказ на 1 USDT и
используй «Повторную отправку Webhook» в кабинете.

Для боя заполни в `.env`: `BOLT_API_KEY` (`bt_live_…`), `BOLT_SECRET`
(Webhook Secret), `PUBLIC_BASE_URL` (для `notifyUrl`), выключи `MOCK_PAYMENTS`.

## Безопасность

- Все секреты (`BOT_TOKEN`, `BOLT_API_KEY`, `BOLT_SECRET`, `LIOG_SECRET`) — только
  в `.env` (в `.gitignore`). В репозитории — лишь `.env.example`.
- Вебхук BoltUtil обрабатывается **только** при верной HMAC-подписи.
- Выдача кода идемпотентна: повторный вебхук/проверка по готовому заказу — no-op.
- Резерв кода из стока атомарный (`SELECT ... FOR UPDATE SKIP LOCKED`), чтобы
  один код не ушёл двум покупателям.

## Тесты

```bash
pip install pytest pytest-asyncio
pytest -q      # 41 тест: подпись LioGames, extract_code, ценообразование, сток/выдача
```

## Дальше (бэклог)

- Alembic-миграции (сейчас схема создаётся через `create_all`).
- Обработка недоплат BoltUtil (`settlementType=MANUAL_UNDERPAID`).
- История покупок пользователя, уведомление админа о фейле выдачи, возвраты.
- Мониторинг баланса LioGames (`balance()`) и алерты о низком стоке.
- Экран/импорт стока пачкой, экспорт заказов.
