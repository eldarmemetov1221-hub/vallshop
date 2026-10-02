"""Наполнить каталог товаром PUBG Mobile UC и его номиналами.

Идемпотентно: повторный запуск не создаёт дубликатов (сверка по
liog_variation_id). Закупочные цены — уровень GOLD из LIOGAMES_API.md.
Наценку/цену продажи админ настраивает потом через бота (/setmarkup, /setprice).

Запуск:
    python -m scripts.seed_pubg
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal

from sqlalchemy import select

from bot.db import Database, Product, Variant

LIOG_PRODUCT_ID = int(os.getenv("LIOG_PRODUCT_ID", "66599"))

# (номинал UC, variation_id, закупочная цена GOLD, USD)
PUBG_VARIANTS = [
    (60, 534124, "0.88"),
    (325, 534125, "4.44"),
    (660, 534126, "8.89"),
    (1800, 534127, "22.24"),
    (3850, 534128, "44.49"),
    (8100, 534129, "88.99"),
    (16200, 534130, "177.99"),
    (24300, 534131, "266.99"),
    (32400, 534132, "355.99"),
    (40500, 534133, "444.99"),
]


async def seed() -> None:
    db = Database(os.getenv("DATABASE_URL", "sqlite+aiosqlite:///vallshop.db"))
    await db.create_all()

    async with db.session() as session:
        product = await session.scalar(
            select(Product).where(Product.title == "PUBG Mobile Code (Global)")
        )
        if product is None:
            product = Product(
                game="PUBG",
                title="PUBG Mobile Code (Global)",
                description="UC-ваучеры PUBG Mobile. Код приходит сразу после оплаты.",
                sort_order=0,
            )
            session.add(product)
            await session.flush()
            print(f"Создан товар id={product.id}")
        else:
            print(f"Товар уже есть id={product.id}")

        existing = set(
            await session.scalars(
                select(Variant.liog_variation_id).where(
                    Variant.product_id == product.id
                )
            )
        )

        created = 0
        for i, (uc, variation_id, cost) in enumerate(PUBG_VARIANTS):
            if variation_id in existing:
                continue
            session.add(
                Variant(
                    product_id=product.id,
                    title=f"{uc} UC",
                    liog_product_id=LIOG_PRODUCT_ID,
                    liog_variation_id=variation_id,
                    cost_usd=Decimal(cost),
                    sort_order=i,
                )
            )
            created += 1
        await session.commit()
        print(f"Добавлено номиналов: {created}")

    await db.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
