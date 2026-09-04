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
from app.handlers import claim, home
from app.services import linking

router = Router(name="common")

claim.register(router)

WELCOME = (
    "👋 <b>OPRAI</b> — conversational DeFi on <b>Robinhood Chain</b>, on Telegram.\n\n"
    "Trade tokenized stocks and tokens, swap, lend, launch, trade perps, run "
    "strategies and read the chain — all on Robinhood Chain, from one chat.\n\n"
    "Get started:\n"
    "• /wallet — your Robinhood wallet\n"
    "• /balance — your ETH balance\n"
    "• /portfolio — your holdings\n"
    "• /help — everything I can do\n"
)

HELP = (
    "<b>OPRAI bot — Robinhood Chain</b>\n\n"
    "/start — get started / link your account\n"
    "/wallet — your wallet · <code>new</code> · <code>import</code> · "
    "<code>export</code> · <code>list</code>\n"
    "/balance — your ETH balance on Robinhood Chain\n"
    "/portfolio — your holdings\n"
    "/send &lt;amount&gt; &lt;token&gt; &lt;0xaddress|@user&gt; — send ETH, tokens or\n"
    "    tokenized stocks (e.g. <code>/send 5 NVDA @friend</code>)\n"
    "/swap &lt;amount&gt; &lt;from&gt; &lt;to&gt; — trade stocks and tokens\n"
    "/bridge &lt;amount&gt; ETH from &lt;chain&gt; — bring funds in\n"
    "/launch &lt;TICKER&gt; &lt;name&gt; — create a token (reply to a photo\n"
    "    to use it as the image)\n"
    "/long &lt;SYM&gt; &lt;$&gt; [x] · /short — leveraged perps (stocks too)\n"
    "/perps — your perps account and positions · /close &lt;SYM&gt;\n"
    "/lend &lt;amount&gt; — earn on USDG · /lend on its own shows rates\n"
    "/borrow &lt;amount&gt; — borrow USDG · /repay · /withdraw\n"
    "/nft — NFTs on Robinhood Chain · /mynfts · /sell\n"
    "/alpha — smart-money alerts · /track &lt;wallet&gt;\n"
    "/copy &lt;wallet&gt; [eth] — copy a wallet's buys, within your limits\n"
    "/token &lt;address|symbol&gt; — on-chain X-ray: holders, whales,\n"
    "    concentration, launchpad, risk score\n"
    "/credits — questions left today\n"
    "/subscribe — OPRAI Pro: a higher daily limit\n"
    "/help — this message\n\n"
    "<b>Just ask.</b> Send me a question — a token, your portfolio, a "
    "strategy — and I'll answer. In a group, mention me or reply to me.\n\n"
    "Only questions are metered; trading commands never are. /subscribe "
    "raises the daily limit (an admin can do it for a group). Actions always "
    "run from your own wallet, confirmed privately."
)


@router.message(CommandStart(deep_link=True))
async def start_with_token(message: Message, command: CommandObject) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    token = (command.args or "").strip()

    # A claim link is the only way someone can be told money is waiting for
    # them — Telegram won't let a bot message a stranger first.
    if token.startswith("claim_"):
        await claim.handle_claim(message, token[len("claim_"):])
        return

    account_id = await linking.consume_link_token(token, user.id) if token else None
    await audit(
        user.id,
        "start_deeplink",
        {"token_present": bool(token), "linked": account_id is not None},
    )
    log.info(
        "start_deeplink",
        telegram_id=user.id,
        token_present=bool(token),
        linked=account_id is not None,
    )
    if account_id:
        await message.answer(
            "✅ <b>Account linked.</b> Your Telegram is now connected to your "
            "OPRAI account — same wallets, same history."
        )
        await home.show(message)
    elif token:
        await message.answer(
            "⚠️ That link is invalid or expired. Generate a fresh one from the "
            "OPRAI app, then tap it again."
        )
        await home.show(message)
    else:
        await home.show(message)


@router.message(CommandStart())
async def start(message: Message) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    await audit(user.id, "start", {})
    log.info("start", telegram_id=user.id, username=user.username)
    await home.show(message)
    # Someone may have sent them something before they had a wallet. Now that
    # they have started us we can finally say so.
    await claim.offer_pending(message)


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await message.answer(HELP)
