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
from aiogram.types import BotCommand

from app.config import settings
from app.db import close_pool, init_pool
from app.handlers import (alpha, bridge, chat, common, copy, flows, home,
                          intel, launch, lend, nft, perps, portfolio, send,
                          swap, wallet)
from app.logging_config import configure_logging, log
from app.services import notify
from app.services.alert_store import AlertStore
from app.services.alert_worker import run_forever as run_alert_worker
from app.services.signals_client import SignalsClient


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(common.router)
    dp.include_router(home.router)
    dp.include_router(flows.router)
    dp.include_router(intel.router)
    dp.include_router(wallet.router)
    dp.include_router(portfolio.router)
    dp.include_router(alpha.router)
    dp.include_router(copy.router)
    dp.include_router(send.router)
    dp.include_router(swap.router)
    dp.include_router(bridge.router)
    dp.include_router(launch.router)
    dp.include_router(perps.router)
    dp.include_router(lend.router)
    dp.include_router(nft.router)
    # Last on purpose: chat's free-text handler is a catch-all, and anything
    # registered after it would never be reached.
    dp.include_router(chat.router)
    return dp


REGISTRY_REFRESH_SECONDS = 6 * 60 * 60
DEPOSIT_POLL_SECONDS = 8

# Telegram's "/" menu is how people find out a command exists at all. Ordered
# by what a new user needs first, not alphabetically.
COMMANDS = [
    BotCommand(command="start", description="Get started"),
    BotCommand(command="wallet", description="Your wallet — new, import, export"),
    BotCommand(command="balance", description="ETH balance"),
    BotCommand(command="portfolio", description="Your holdings"),
    BotCommand(command="send", description="Send ETH, tokens or stocks"),
    BotCommand(command="swap", description="Trade stocks and tokens"),
    BotCommand(command="bridge", description="Bring funds in from another chain"),
    BotCommand(command="long", description="Open a leveraged long"),
    BotCommand(command="short", description="Open a leveraged short"),
    BotCommand(command="perps", description="Perps account and positions"),
    BotCommand(command="close", description="Close a position"),
    BotCommand(command="lend", description="Earn on USDG / see rates"),
    BotCommand(command="borrow", description="Borrow against collateral"),
    BotCommand(command="alpha", description="Smart-money alerts"),
    BotCommand(command="track", description="Watch a wallet"),
    BotCommand(command="copy", description="Copy-trade a wallet"),
    BotCommand(command="nft", description="NFTs on Robinhood Chain"),
    BotCommand(command="mynfts", description="NFTs you hold"),
    BotCommand(command="launch", description="Create a token"),
    BotCommand(command="token", description="Analyse a token"),
    BotCommand(command="ask", description="Ask OPRAI anything"),
    BotCommand(command="credits", description="Conversation credits left"),
    BotCommand(command="help", description="All commands"),
]


async def _watch_deposits(bot) -> None:
    """Tell people when money lands, without them asking.

    We run the chain's node, so a deposit is visible within a block or two.
    Runs beside polling and never lets a bad cycle stop the bot.
    """
    from app.services import deposits

    while True:
        try:
            for d in await deposits.poll():
                await notify.send(
                    bot, d.telegram_id,
                    f"💰 <b>{d.display}</b> landed in your OPRAI wallet.\n"
                    "Check /balance, or put it to work with /swap.",
                )
        except Exception as e:  # noqa: BLE001
            log.warning("deposit_watch_failed", error=str(e))

        # A credit top-up whose receipt outlived its confirmation wait is a
        # debt: the user paid and is owed credits. Settle it here rather than
        # leaving it for someone to notice.
        try:
            from app.services import topups

            for done in await topups.settle_pending():
                await notify.send(
                    bot, done["telegram_id"],
                    f"✅ <b>{done['credits']} credits added</b> — your "
                    "payment confirmed.",
                )
        except Exception as e:  # noqa: BLE001
            log.warning("topup_settle_failed", error=str(e))

        # A claim nobody came for shouldn't hang over the sender's wallet for
        # ever — close it and say so, since they were told to keep the funds.
        try:
            from app.services import claims

            for gone in await claims.expire_stale():
                amount = claims.display(int(gone["amount_base"]), gone["decimals"])
                await notify.send(
                    bot, gone["from_telegram_id"],
                    f"⌛ @{gone['to_username']} never claimed your {amount} "
                    f"{gone['symbol']}, so that link has expired. The funds "
                    "never left your wallet.",
                )
        except Exception as e:  # noqa: BLE001
            log.warning("claim_expiry_failed", error=str(e))

        await asyncio.sleep(DEPOSIT_POLL_SECONDS)


LEADER_REFRESH_SECONDS = 20


async def _run_copy_trading(bot) -> None:
    """Watch the wallets people copy, and copy their buys.

    Everything for this existed — the store, the risk engine, the block
    watcher — but nothing ever started it, so /copy accepted subscriptions
    that could never fire. The engine's own limits (per-trade ETH, daily USD)
    are what make auto-execution safe, so they are applied before every buy
    and the ETH price behind the dollar cap is read live rather than assumed.
    """
    from app.services import copy_executor
    from app.services.copy_engine import CopyEngine
    from app.services.copy_store import CopyStore
    from app.services.copy_watcher import watch_buys

    store = CopyStore()

    async def tell(telegram_id: int, text: str) -> None:
        await notify.send(bot, telegram_id, text)

    engine = CopyEngine(
        store, copy_executor.buy, tell,
        price_provider=copy_executor.eth_price_usd,
    )

    # The watcher asks for the live set on every block, and asking the database
    # that often would be wasteful — so it is refreshed on its own cadence and
    # read from memory. A wallet added now starts being copied within seconds.
    leaders: set[str] = set()

    async def refresh() -> None:
        nonlocal leaders
        while True:
            try:
                leaders = {w.lower() for w in await store.all_leaders()}
            except Exception as e:  # noqa: BLE001
                log.warning("copy_leaders_refresh_failed", error=str(e))
            await asyncio.sleep(LEADER_REFRESH_SECONDS)

    asyncio.create_task(refresh())
    await watch_buys(settings.robinhood_rpc(), lambda: leaders, engine.on_buy)


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

    # Anyone left on "Thinking…" by the restart that just happened.
    await chat.close_orphaned_turns(bot)

    try:
        await bot.set_my_commands(COMMANDS)
    except Exception as e:  # noqa: BLE001 — a missing menu must not stop the bot
        log.warning("set_commands_failed", error=str(e))

    # Background alpha-alert worker: polls the chain-intel signal feed and pings
    # subscribers. Isolated task — its own retry loop; a crash never stops the bot.
    from app.handlers.alpha import make_alert_sender
    worker = asyncio.create_task(
        run_alert_worker(SignalsClient(), AlertStore(), make_alert_sender(bot)))
    registry_task = asyncio.create_task(_keep_token_registry_fresh())
    deposit_task = asyncio.create_task(_watch_deposits(bot))
    copy_task = asyncio.create_task(_run_copy_trading(bot))

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        worker.cancel()
        registry_task.cancel()
        deposit_task.cancel()
        copy_task.cancel()
        await bot.session.close()
        await close_pool()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
