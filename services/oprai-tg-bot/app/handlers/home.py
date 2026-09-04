"""The home screen — what /start opens onto.

A wall of slash commands makes someone read a manual before they can do
anything. This is the same product behind a screen: what you hold, right at
the top, and a button for everything worth doing.

Buttons that only read the chain run immediately, because there is nothing to
ask. Buttons that spend money can't — an amount and a token have to come from
the person — so those answer with the exact command to send, already filled in
with a realistic example rather than angle brackets to decode.

Everything here is private-by-default in the same way the rest of the bot is:
the balance goes to the person, never the room.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db import audit
from app.logging_config import log
from app.services import auth as auth_svc
from app.services import lighter, morpho
from app.services import portfolio as pf
from app.services import wallet as wallet_svc

router = Router(name="home")


def keyboard() -> InlineKeyboardMarkup:
    """The home grid. Defined in `menu` so the icons and the flows that open
    from them stay in one place."""
    from app.handlers.menu import home_keyboard

    return home_keyboard()


async def card(telegram_id: int) -> str:
    """Balance first, because it is the thing people open the bot to see.

    Every line is read independently — a provider having a bad minute costs its
    own line, not the whole screen.
    """
    address = await wallet_svc.wallet_address(telegram_id)
    try:
        jwt = await auth_svc.get_jwt(telegram_id)
    except auth_svc.AuthError:
        jwt = ""

    native, tokens, lending, perps = await asyncio.gather(
        pf.native_balance(telegram_id),
        pf.token_holdings(address, telegram_id),
        morpho.positions(jwt, address) if jwt else _none(),
        lighter.account(jwt, address) if jwt else _none(),
        return_exceptions=True,
    )

    lines = ["👋 <b>OPRAI</b> · Robinhood Chain", ""]

    if isinstance(native, Exception):
        lines.append("⬡ ETH — couldn't read it just now")
    else:
        lines.append(f"⬡ <b>{native['eth']:.4f} ETH</b>")

    if not isinstance(tokens, Exception) and tokens:
        stocks = [t for t in tokens if t["is_stock"]]
        others = [t for t in tokens if not t["is_stock"]]
        if stocks:
            lines.append(
                "📊 " + " · ".join(f"{t['display']} {t['symbol']}" for t in stocks[:4])
                + (f" +{len(stocks) - 4}" if len(stocks) > 4 else "")
            )
        if others:
            lines.append(
                "🪙 " + " · ".join(f"{t['display']} {t['symbol']}" for t in others[:4])
                + (f" +{len(others) - 4}" if len(others) > 4 else "")
            )

    if not isinstance(lending, Exception) and lending:
        supplied = sum(float(p.get("supplyAssets") or 0) for p in lending)
        borrowed = sum(float(p.get("borrowAssets") or 0) for p in lending)
        bits = []
        if supplied:
            bits.append(f"lent {supplied:,.2f}")
        if borrowed:
            bits.append(f"borrowed {borrowed:,.2f}")
        if bits:
            lines.append("🏦 " + " · ".join(bits) + " USDG")

    if not isinstance(perps, Exception) and perps and perps.get("has_account"):
        open_positions = [
            p for p in (perps.get("positions") or []) if float(p.get("size") or 0) > 0
        ]
        collateral = float(perps.get("collateral") or 0)
        # An empty perps account is not a position; a "$0.00" line is noise on
        # the one screen that should say what you actually have.
        if collateral > 0 or open_positions:
            lines.append(
                f"📈 ${collateral:,.2f} collateral"
                + (f" · {len(open_positions)} open" if open_positions else "")
            )

    lines += ["", f"<code>{address}</code>"]
    return "\n".join(lines)


async def _none():
    return None


async def show(message: Message) -> None:
    """Open the home screen. Used by /start and by the Refresh button."""
    from app.handlers.privacy import private_answer

    await private_answer(message, await card(message.from_user.id),
                         reply_markup=keyboard())


# What a button does when it needs input we don't have. An example beats a
# grammar: nobody wants to work out what <amount> should look like.
PROMPTS = {
    "swap": ("🔄 <b>Swap</b>\n\nSend it like this:\n"
             "<code>/swap 0.05 ETH NVDA</code> — buy a stock\n"
             "<code>/swap 5 NVDA USDG</code> — sell one\n"
             "<code>/swap 0.05 ETH USDG</code>"),
    "send": ("📤 <b>Send</b>\n\nSend it like this:\n"
             "<code>/send 5 NVDA @friend</code>\n"
             "<code>/send 0.01 ETH 0xAbC…</code>\n\n"
             "<i>They don't need an account — I'll give you a link to pass on.</i>"),
    "launch": ("🚀 <b>Launch a token</b>\n\n"
               "<code>/launch SLR Solar Token</code>\n\n"
               "<i>Reply to a photo and it becomes the token's image. "
               "You pick the venue: a Pons bonding curve, or an instant "
               "Uniswap pool on pools.trade.</i>"),
    "bridge": ("🌉 <b>Bring funds in</b>\n\n"
               "<code>/bridge 0.05 ETH from base</code>\n\n"
               "<i>Also works from Ethereum, Arbitrum, Optimism, Polygon and BNB.</i>"),
    "ask": ("💬 <b>Ask me anything</b>\n\n"
            "Just type it — no command needed:\n"
            "<i>\"is NVDA worth holding here?\"</i>\n"
            "<i>\"what can I earn on USDG?\"</i>\n"
            "<i>\"analyse 0xabc…\"</i>"),
}

# Buttons that only read: run the command they stand for, right now.
READS = {
    "portfolio": ("portfolio", "portfolio_cmd"),
    "lend": ("lend", "lend_router"),
    "perps": ("perps", "perps_cmd"),
    "nft": ("nft", "nft_cmd"),
    "alpha": ("alpha", "alpha_menu"),
    "credits": ("chat", "credits_cmd"),
}


def as_person(cb: CallbackQuery) -> Message:
    """The callback's message, attributed to whoever pressed the button.

    aiogram models are frozen, so this has to be a copy — assigning to
    `from_user` raises a ValidationError that escapes the handler and stops
    the bot processing updates at all. The bot binding is carried over so the
    copy can still answer.
    """
    return cb.message.model_copy(update={"from_user": cb.from_user}).as_(cb.bot)


class _Args:
    """Stand-in for aiogram's CommandObject: a button carries no arguments."""

    args = None
    command = None
    prefix = "/"
    mention = None
    magic_result = None


@router.callback_query(F.data.startswith("home:"))
async def home_button(cb: CallbackQuery) -> None:
    what = cb.data.split(":", 1)[1]
    await cb.answer()
    message = cb.message

    # The message a callback carries was sent by us, so anything downstream
    # that reads from_user would see the bot. aiogram's Message is frozen, so
    # this is a copy with the real person on it — assigning raises and takes
    # every update down with it.
    message = as_person(cb)

    if what == "wallet":
        from app.handlers.flows import wallets_menu

        await wallets_menu(cb)
        return

    if what == "refresh":
        try:
            await message.edit_text(await card(cb.from_user.id),
                                    reply_markup=keyboard())
        except Exception:  # noqa: BLE001 — identical text is not an error
            pass
        return

    if what == "help":
        from app.handlers.common import HELP

        await message.answer(HELP)
        return

    if what in PROMPTS:
        await message.answer(PROMPTS[what])
        return

    handler = READS.get(what)
    if handler is None:
        return
    module_name, func_name = handler
    module = __import__(f"app.handlers.{module_name}", fromlist=[func_name])
    func = getattr(module, func_name)
    await audit(cb.from_user.id, "home_button", {"what": what})
    try:
        # Some take a CommandObject, some don't — the stand-in covers both.
        try:
            await func(message, _Args())
        except TypeError:
            await func(message)
    except Exception as e:  # noqa: BLE001 — a broken button must not kill the screen
        log.warning("home_button_failed", what=what, error=str(e)[:200])
        await message.answer("⚠️ Couldn't open that just now — try the command directly.")
