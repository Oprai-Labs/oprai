"""OPRAI Telegram bot entrypoint.

Faz 0: dev uses long-polling. A webhook runner (aiohttp behind Caddy) lands in
Faz 4 for prod. The bot holds no keys — custody + signing live in the isolated
Rust signer (OPRAI_TG_SIGNER_URL).
"""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import settings
from app.db import close_pool, init_pool
from app.handlers import alpha, common, portfolio, wallet
from app.logging_config import configure_logging, log
from app.services.alert_store import AlertStore
from app.services.alert_worker import run_forever as run_alert_worker
from app.services.signals_client import SignalsClient


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(common.router)
    dp.include_router(wallet.router)
    dp.include_router(portfolio.router)
    dp.include_router(alpha.router)
    return dp


async def run() -> None:
    configure_logging(settings.LOG_LEVEL)
    if not settings.OPRAI_TELEGRAM_BOT_TOKEN:
        raise SystemExit(
            "OPRAI_TELEGRAM_BOT_TOKEN is not set — put it in .env (never in the repo)."
        )

    await init_pool()
    bot = Bot(
        token=settings.OPRAI_TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher()

    me = await bot.get_me()
    log.info("bot_starting", username=me.username, id=me.id, mode="long-polling")

    # Background alpha-alert worker: polls the chain-intel signal feed and pings
    # subscribers. Isolated task — its own retry loop; a crash never stops the bot.
    from app.handlers.alpha import make_alert_sender
    worker = asyncio.create_task(
        run_alert_worker(SignalsClient(), AlertStore(), make_alert_sender(bot)))

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        worker.cancel()
        await bot.session.close()
        await close_pool()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
