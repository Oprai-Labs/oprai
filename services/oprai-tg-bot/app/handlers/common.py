"""Core command handlers: /start, /help.

Faz 0 scope. /start upserts the tg_users identity row and (when a deep-link
token is present, e.g. t.me/OpraiBot?start=<token>) will bind the Telegram
identity to an existing OPRAI account — that binding lands in 0.7. Wallet
create/import and balance/portfolio land in 0.5 once the signer (0.3) is up.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from app.db import audit, upsert_tg_user
from app.logging_config import log

router = Router(name="common")

WELCOME = (
    "👋 <b>OPRAI</b> — conversational DeFi, now on Telegram.\n\n"
    "Swap, bridge, lend, stake, trade perps, launch tokens and read the chain "
    "across Solana and EVM chains — from one chat.\n\n"
    "Setup is coming online. For now:\n"
    "• /help — what I can do\n"
    "• /wallet — your OPRAI wallet <i>(soon)</i>\n"
    "• /balance — holdings <i>(soon)</i>\n"
)

HELP = (
    "<b>OPRAI bot — commands</b>\n\n"
    "/start — get started / link your account\n"
    "/wallet — create or import your custodial wallet <i>(soon)</i>\n"
    "/balance — your token balances <i>(soon)</i>\n"
    "/portfolio — full portfolio across chains <i>(soon)</i>\n"
    "/help — this message\n\n"
    "In groups, add me and an admin can top up the group's free quota with "
    "$OPRAI. Actions always run from your own wallet, confirmed privately."
)


@router.message(CommandStart(deep_link=True))
async def start_with_token(message: Message, command: CommandObject) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    token = (command.args or "").strip()
    # 0.7 will consume `token` against tg_link_tokens and bind linked_account_id.
    await audit(user.id, "start_deeplink", {"token_present": bool(token)})
    log.info("start_deeplink", telegram_id=user.id, token_present=bool(token))
    await message.answer(WELCOME)


@router.message(CommandStart())
async def start(message: Message) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    await audit(user.id, "start", {})
    log.info("start", telegram_id=user.id, username=user.username)
    await message.answer(WELCOME)


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await message.answer(HELP)
