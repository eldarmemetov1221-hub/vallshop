# Деплой VallShop на сервер (Docker Compose)

Пошагово. Сначала запустим бота **без домена** (оплата подтверждается кнопкой
«Проверить оплату») — это рабочий вариант. Потом, когда будет домен, добавим
вебхук BoltUtil одной командой (мгновенное подтверждение оплаты).

---

## 0. Что такое «домен» и нужен ли он сейчас

**Домен** — это адрес сайта, например `shop.example.com`. Он нужен BoltUtil,
чтобы присылать твоему серверу уведомление об оплате (**вебхук**) по HTTPS:
BoltUtil дёргает `https://ТВОЙ-ДОМЕН/bolt/webhook`, и заказ выдаётся сам.

Как понять, есть ли он у тебя:
- Если ты **покупал домен** (на reg.ru, namecheap, Cloudflare и т.п.) — он есть.
- Голый IP-адрес сервера (например `203.0.113.10`) **доменом не является**:
  Let's Encrypt не выдаёт на него HTTPS-сертификат, значит вебхук не поднять.

**Вывод:** домен не обязателен для старта. Запускаемся без него (шаги 1–6),
оплата идёт через кнопку в боте. Домен добавим позже (шаг 7) — купить дешёвый
домен/поддомен и направить его A-записью на IP сервера.

---

## 1. Сервер и доступ

Нужен VPS с Linux (рекомендую **Ubuntu 22.04/24.04**). Подключись по SSH:

```bash
ssh root@IP_СЕРВЕРА
```

## 2. Установка Docker

```bash
curl -fsSL https://get.docker.com | sh
docker --version && docker compose version
```

## 3. Получить код

Репозиторий приватный — склонируй его (понадобится доступ к GitHub с сервера,
проще всего через Personal Access Token или SSH-ключ):

```bash
cd /opt
git clone https://github.com/eldarmemetov1221-hub/vallshop.git
cd vallshop
```

> Если с git на сервере возится не хочется — можно скачать zip репозитория с
> GitHub и распаковать в `/opt/vallshop`.

## 4. Заполнить .env

```bash
cp .env.example .env
nano .env     # отредактировать
```

Минимум для старта (без домена):

| Переменная | Что вписать |
|---|---|
| `BOT_TOKEN` | токен от **@BotFather** (создай бота: `/newbot`) |
| `ADMIN_IDS` | твой Telegram id (узнать: напиши **@userinfobot**) |
| `BOLT_API_KEY` | API-ключ BoltUtil (`bt_live_...`) |
| `BOLT_SECRET` | **Webhook Secret** из кабинета BoltUtil |
| `BOLT_NETWORK` | `TRC20` (рекомендуется — дёшево и быстро) |
| `MOCK_PAYMENTS` | `0` (реальная оплата) |

Остальное можно оставить по умолчанию. `PUBLIC_BASE_URL` пока **оставь пустым** —
бот запустится в режиме без вебхука.

> Хочешь сначала всё проверить без реальных денег — поставь `MOCK_PAYMENTS=1`
> и `LIOG_MOCK=1`: бот выдаст тестовый код по кнопке «Проверить оплату».

## 5. Запуск

```bash
docker compose up -d --build
docker compose exec bot python -m scripts.seed_pubg   # создать каталог PUBG
docker compose logs -f bot                            # смотреть логи
```

Бот должен ответить в Telegram на `/start`.

## 6. Настроить магазин (в чате с ботом, от админа)

```
/admin                      — меню и статистика
/variants 1                 — посмотреть номиналы PUBG с id
/setmarkup 2 20             — наценка 20% на номинал id=2
/addstock 2                 — добавить коды (далее прислать коды построчно)
/stock                      — проверить остатки
```

> Напоминание BoltUtil: минимальная сумма оплаты — **1 USDT**. Номинал 60 UC
> (закуп $0.88) продавать можно только с ценой ≥ 1 USDT.

На этом магазин работает: покупатель выбирает товар → платит USDT → жмёт
«Проверить оплату» → получает код.

---

## 7. (Позже) Домен + вебхук BoltUtil

Когда появится домен (например `shop.example.com`):

1. В панели регистратора добавь **A-запись**: `shop.example.com → IP сервера`.
2. Открой порты 80 и 443 на сервере (firewall/облако).
3. В `.env` добавь:
   ```
   DOMAIN=shop.example.com
   PUBLIC_BASE_URL=https://shop.example.com
   ```
4. Перезапусти с Caddy (авто-HTTPS):
   ```bash
   docker compose -f docker-compose.yml -f deploy/docker-compose.webhook.yml up -d --build
   ```
5. В кабинете BoltUtil укажи **notifyUrl / Webhook URL**:
   `https://shop.example.com/bolt/webhook`
6. Проверь: `curl https://shop.example.com/healthz` → `{"ok": true}`.

Теперь оплата подтверждается вебхуком автоматически (кнопка тоже продолжит
работать как резерв).

---

## Обслуживание

```bash
# Обновить до новой версии:
cd /opt/vallshop && git pull
docker compose up -d --build

# Логи:
docker compose logs -f bot

# Бэкап БД (сток/заказы):
cp data/vallshop.db data/vallshop.db.bak

# Остановить / запустить:
docker compose down
docker compose up -d
```

## Безопасность

- `.env` с секретами **не коммитить** (он в `.gitignore`).
- Для `git clone` на сервере используй токен с доступом только на чтение и удали
  его из истории команд (`history -c`), либо деплой-ключ.
- Если API-ключ BoltUtil где-то засветился — перевыпусти его в кабинете.
