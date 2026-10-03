"""Импорт кодов активации из старого SQLite (robloxbot codes.db) в VallShop.

Коды никуда не отправляются — читаются локально на сервере и пишутся в базу
VallShop. Статусы переводятся: свободен→free, использован→used.

Использование (внутри контейнера бота):
    # сначала скопируй codes.db в ./data (она смонтирована как /app/data):
    #   cp /root/root/tg_bot/codes.db /opt/vallshop/data/codes.db
    docker compose ... exec bot python -m scripts.import_codes
    # или указать путь явно:
    docker compose ... exec bot python -m scripts.import_codes /app/data/codes.db
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys

from bot.config import BotConfig
from bot.db import Database
from bot.db.models import ActivationCode
from sqlalchemy import select
from roblox import parse_robux_amount

_STATUS_MAP = {"свободен": "free", "использован": "used"}


async def main(src_path: str) -> None:
    rows = sqlite3.connect(src_path).execute(
        "SELECT code, product, status, instruction FROM codes"
    ).fetchall()
    print(f"В источнике кодов: {len(rows)}")

    config = BotConfig.from_env()
    db = Database(config.database_url)
    await db.create_all()

    async with db.session() as session:
        existing = set(await session.scalars(select(ActivationCode.code)))
        added = skipped = 0
        for code, product, status, instruction in rows:
            if not code or code in existing:
                skipped += 1
                continue
            session.add(ActivationCode(
                code=code,
                product=product or "",
                instruction=instruction,
                robux_amount=parse_robux_amount(product),
                status=_STATUS_MAP.get((status or "").strip(), "used"),
            ))
            existing.add(code)
            added += 1
            if added % 500 == 0:
                await session.flush()
        await session.commit()

    await db.dispose()
    print(f"✅ Импортировано: {added}, пропущено (дубли/пустые): {skipped}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/app/data/codes.db"
    asyncio.run(main(path))
