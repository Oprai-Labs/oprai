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
from app.handlers import alpha, common, portfolio, send, wallet
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
    dp.include_router(send.router)
    return dp


REGISTRY_REFRESH_SECONDS = 6 * 60 * 60


async def _keep_token_registry_fresh() -> None:
    """Seed the token registry on first boot, then refresh it periodically.

    Runs beside polling: a registry hiccup must never stop the bot answering,
    so failures are logged and retried on the next cycle.
    """
    from app.services import tokens

    while True:
        try:
            if await tokens.registry_size() == 0:
                log.info("token_registry_seeding")
            result = await tokens.sync_registry()
            log.info("token_registry_ready", **result)
        except Exception as e:  # noqa: BLE001 — never let this kill the bot
            log.warning("token_registry_sync_failed", error=str(e))
        await asyncio.sleep(REGISTRY_REFRESH_SECONDS)


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
    registry_task = asyncio.create_task(_keep_token_registry_fresh())

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        worker.cancel()
        registry_task.cancel()
        await bot.session.close()
        await close_pool()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
