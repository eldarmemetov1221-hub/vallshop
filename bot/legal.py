"""Публичные страницы магазина: оферта и простой лендинг.

Отдаются aiohttp-сервером (см. bot.web) по адресам /offer и / на публичном
домене. Контакты/реквизиты берутся из конфига (SUPPORT_CONTACT, LEGAL_ENTITY).
Нужны для подключения платёжного провайдера (публичные условия оплаты,
сроков выдачи и возврата).
"""

from __future__ import annotations

import html
from datetime import date

SHOP_NAME = "VallShop"

_STYLE = """
:root{color-scheme:light dark;--bg:#0f1115;--card:#171a21;--fg:#e8eaed;
--muted:#9aa0a6;--accent:#f0a36b;--line:#272b33}
@media (prefers-color-scheme:light){:root{--bg:#f6f7f9;--card:#fff;
--fg:#1b1f24;--muted:#5f6368;--accent:#d9823b;--line:#e6e8eb}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.65 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:32px 16px 72px}
header{display:flex;align-items:center;gap:14px;margin-bottom:8px}
.logo{width:46px;height:46px;border-radius:50%;
background:radial-gradient(circle at 35% 30%,var(--accent),#7a4a22);flex:0 0 auto}
h1{font-size:26px;margin:0}
.sub{color:var(--muted);margin:2px 0 26px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:22px 24px;margin:16px 0}
h2{font-size:19px;margin:26px 0 8px}
h2:first-child{margin-top:0}
ul{margin:8px 0 8px 2px;padding-left:20px}
li{margin:4px 0}
a{color:var(--accent)}
.muted{color:var(--muted)}
footer{color:var(--muted);font-size:14px;margin-top:28px;text-align:center}
code{background:rgba(127,127,127,.15);padding:1px 6px;border-radius:6px}
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><div class='wrap'><header><div class='logo'></div>"
        f"<h1>{SHOP_NAME}</h1></header>{body}</div></body></html>"
    )


def _contacts_block(config) -> str:
    support = (config.support_contact or "").strip()
    if support.startswith("@"):
        link = f"<a href='https://t.me/{html.escape(support[1:])}'>{html.escape(support)}</a>"
    elif support.startswith("http"):
        link = f"<a href='{html.escape(support)}'>{html.escape(support)}</a>"
    elif "@" in support:
        link = f"<a href='mailto:{html.escape(support)}'>{html.escape(support)}</a>"
    elif support:
        link = html.escape(support)
    else:
        link = "—"
    legal = html.escape((config.legal_entity or "").strip()) or "—"
    return (
        "<h2>9. Контакты и реквизиты</h2>"
        f"<ul><li>Поддержка: {link}</li>"
        f"<li>Продавец: {legal}</li></ul>"
    )


def landing_html(config) -> str:
    bot_link = ""
    body = (
        "<p class='sub'>Магазин цифровых товаров: игровые ключи, "
        "подарочные карты и пополнения игр. Оплата в USDT.</p>"
        "<div class='card'>"
        "<h2>Как это работает</h2>"
        "<ul>"
        "<li>Пополняете баланс в USDT.</li>"
        "<li>Покупаете товар — он приходит прямо в чат бота.</li>"
        "<li>Выдача мгновенная из наличия или под заказ за пару минут.</li>"
        "</ul>"
        f"{bot_link}"
        "<p><a href='/offer'>Публичная оферта и условия покупки →</a></p>"
        "</div>"
    )
    return _page(f"{SHOP_NAME} — цифровые товары", body)


def offer_html(config) -> str:
    today = date.today().strftime("%d.%m.%Y")
    body = (
        "<p class='sub'>Публичная оферта (условия покупки). "
        f"Редакция от {today}.</p>"
        "<div class='card'>"

        "<h2>1. Общие положения</h2>"
        f"<p>Настоящий документ — публичная оферта интернет-магазина "
        f"{SHOP_NAME} (далее — «Магазин»), работающего через Telegram-бота. "
        "Оформляя заказ или пополняя баланс, покупатель (далее — «Клиент») "
        "принимает условия настоящей оферты в полном объёме.</p>"

        "<h2>2. Предмет</h2>"
        "<p>Магазин продаёт <b>цифровые товары</b>: игровые ключи, "
        "подарочные карты (коды) и внутриигровые пополнения. Товар является "
        "цифровым и передаётся в электронном виде.</p>"

        "<h2>3. Цены и оплата</h2>"
        "<ul>"
        "<li>Все цены указаны в USDT (стейблкоин, ≈ USD).</li>"
        "<li>Оплата производится в криптовалюте USDT через платёжного "
        "провайдера. Клиент пополняет внутренний баланс, с которого "
        "оплачиваются покупки.</li>"
        "<li>Баланс зачисляется автоматически после подтверждения оплаты "
        "в сети блокчейн.</li>"
        "</ul>"

        "<h2>4. Порядок и сроки выдачи</h2>"
        "<ul>"
        "<li>Товар выдаётся автоматически в чат бота сразу после оплаты "
        "с баланса.</li>"
        "<li>Если товар есть в наличии — выдача мгновенная.</li>"
        "<li>Если товар оформляется у поставщика (под заказ) — выдача "
        "обычно занимает до нескольких минут; код придёт в чат бота.</li>"
        "<li>Внутриигровые пополнения зачисляются напрямую на игровой "
        "аккаунт Клиента по указанным им данным (ID игрока и т.п.).</li>"
        "</ul>"

        "<h2>5. Возврат средств и гарантии</h2>"
        "<ul>"
        "<li>Если товар <b>не был выдан</b> (нет в наличии, ошибка "
        "поставщика и т.п.), средства <b>автоматически возвращаются на "
        "баланс</b> Клиента в Магазине и могут быть использованы для "
        "других покупок.</li>"
        "<li>Качественный цифровой товар, который был успешно выдан "
        "(код/ключ показан Клиенту либо пополнение зачислено), "
        "<b>возврату и обмену не подлежит</b>, так как является цифровым "
        "и не может быть возвращён после передачи.</li>"
        "<li>Если выданный код оказался нерабочим не по вине Клиента — "
        "обратитесь в поддержку; такие случаи рассматриваются "
        "индивидуально (замена товара или возврат на баланс).</li>"
        "</ul>"

        "<h2>6. Обязанности Клиента</h2>"
        "<ul>"
        "<li>Клиент самостоятельно и корректно вводит данные для "
        "пополнений (ID игрока, сервер и т.п.).</li>"
        "<li>Магазин не несёт ответственности за неверно указанные "
        "Клиентом данные — возврат за такой заказ не производится.</li>"
        "</ul>"

        "<h2>7. Ответственность</h2>"
        "<p>Магазин не несёт ответственности за действия third-party "
        "платформ (блокировки игровых аккаунтов правообладателем и т.п.). "
        "Клиент использует приобретённые товары в соответствии с правилами "
        "соответствующих платформ.</p>"

        "<h2>8. Персональные данные</h2>"
        "<p>Магазин обрабатывает минимально необходимые данные "
        "(Telegram ID, данные для выдачи заказа) исключительно для "
        "исполнения заказа и не передаёт их третьим лицам, кроме "
        "поставщиков товара и платёжного провайдера.</p>"

        + _contacts_block(config) +

        "</div>"
        f"<footer>© {date.today().year} {SHOP_NAME}. Все права защищены.</footer>"
    )
    return _page(f"{SHOP_NAME} — Публичная оферта", body)
