"""Точка входа Telegram-бота VallShop."""

from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from liogames import LioGamesClient
from fazercard import FazerCardClient

from .config import BotConfig
from .db import Database
from .handlers import build_root_router
from .payments import build_provider
from .services.poller import run_fulfillment_poller, run_topup_poller

log = logging.getLogger("vallshop")


async def _run_web(config: BotConfig, bot, db, provider, liog) -> None:
    """Запустить aiohttp-сервер вебхуков, если задан PUBLIC_BASE_URL."""
    from aiohttp import web

    from .web import build_app

    app = build_app(bot=bot, db=db, provider=provider, liog=liog)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    log.info("Webhook-сервер слушает :%s (notifyUrl=%s)", port, config.notify_url)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = BotConfig.from_env()

    db = Database(config.database_url)
    await db.create_all()

    provider = build_provider(config)
    liog = LioGamesClient.from_env()
    fzr = FazerCardClient.from_env()

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Внедрение зависимостей в хендлеры (aiogram прокидывает по имени параметра).
    dp["config"] = config
    dp["db"] = db
    dp["provider"] = provider
    dp["liog"] = liog
    dp["fzr"] = fzr

    dp.include_router(build_root_router())

    tasks = [
        asyncio.create_task(run_fulfillment_poller(bot, db, liog, fzr)),
        asyncio.create_task(run_topup_poller(bot, db, provider)),
    ]
    if config.public_base_url:
        await _run_web(config, bot, db, provider, liog)

    log.info(
        "Старт бота. Провайдер оплаты: %s. Админы: %s",
        provider.name,
        config.admin_ids,
    )
    try:
        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()
        await bot.session.close()
        await db.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
