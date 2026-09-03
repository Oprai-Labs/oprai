"""/launch — create a token on Robinhood Chain.

Reply to a photo with `/launch TICKER Name` and that photo becomes the token's
image: the picture someone just posted is usually the whole idea, and making
them leave chat to host it kills the moment.

Two launchpads, and the difference is real, so the user picks:
  • Pons — a bonding curve: price starts low and rises as people buy, and the
    token graduates to a pool later.
  • pools.trade — no curve; it opens a Uniswap pool immediately, so the token
    trades like any other from the first block.

They also want the image differently: Pons stores a URL on-chain (so we host it
somewhere that stays fetchable), pools.trade takes the bytes inline and pins
them itself.
"""

from __future__ import annotations

import secrets
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db import audit, upsert_tg_user
from app.logging_config import log
from app.services import auth as auth_svc
from app.services import evm, launch
from app.services import portfolio as pf
from app.services import wallet as wallet_svc
from app.signer_client import SignerError

router = Router(name="launch")

WEI = 10**18
EXPLORER = "https://robinscan.io/tx/"
TOKEN_EXPLORER = "https://robinscan.io/token/"
_pending: dict[str, dict] = {}

USAGE = (
    "Usage: <code>/launch &lt;TICKER&gt; &lt;name&gt;</code>\n"
    "Example: <code>/launch SLR Solar Token</code>\n\n"
    "<i>Reply to a photo with the command and it becomes the token's image.</i>"
)


def _fmt_eth(wei: int) -> str:
    return f"{Decimal(wei) / WEI:.6f}".rstrip("0").rstrip(".") or "0"


async def _image_from(message: Message) -> bytes | None:
    """The replied-to photo, if there is one."""
    src = message.reply_to_message
    photo = (src.photo[-1] if src and src.photo else None) or (
        message.photo[-1] if message.photo else None
    )
    if not photo:
        return None
    buf = await message.bot.download(photo.file_id)
    return buf.read()


def _description_from(message: Message, name: str) -> str:
    """The quoted message's own words describe the token.

    Replying to a post is how these launches actually start, so the post is the
    description. pools.trade requires a non-empty one, and falling back to the
    name keeps a launch from failing validation over a blank field."""
    src = message.reply_to_message
    text = ((src.text or src.caption) if src else None) or ""
    text = " ".join(text.split())[:500]
    return text or name


@router.message(Command("launch"))
async def launch_cmd(message: Message, command: CommandObject) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    args = (command.args or "").split()
    if len(args) < 2:
        await message.answer(USAGE)
        return

    symbol = args[0].upper().lstrip("$")[:16]
    name = " ".join(args[1:])[:64]
    if not symbol.isalnum():
        await message.answer("A ticker should be letters and numbers only.\n\n" + USAGE)
        return

    image = await _image_from(message)
    description = _description_from(message, name)
    pid = secrets.token_urlsafe(8)
    _pending[pid] = {
        "telegram_id": user.id, "symbol": symbol, "name": name, "image": image,
        "description": description,
    }
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Bonding curve (Pons)", callback_data=f"lnch:v:pons:{pid}")],
        [InlineKeyboardButton(text="⚡ Instant pool (pools.trade)", callback_data=f"lnch:v:pools:{pid}")],
        [InlineKeyboardButton(text="Cancel", callback_data=f"lnch:no:{pid}")],
    ])
    await audit(user.id, "launch_started", {"symbol": symbol, "name": name})
    await message.answer(
        f"<b>${symbol}</b> — {name}\n"
        f"Image: {'attached ✅' if image else 'none'}\n"
        f"About: <i>{description[:120]}</i>\n\n"
        "How should it launch?\n\n"
        "📈 <b>Bonding curve</b> — price starts low and climbs as people buy; "
        "it graduates to a pool later.\n"
        "⚡ <b>Instant pool</b> — a Uniswap pool from the first block, trading "
        "like any other token straight away.",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("lnch:v:"))
async def launch_choose_venue(cb: CallbackQuery) -> None:
    _, _, venue, pid = cb.data.split(":", 3)
    p = _pending.get(pid)
    if not p:
        await cb.answer("This launch expired. Run /launch again.", show_alert=True)
        return
    if cb.from_user.id != p["telegram_id"]:
        await cb.answer("This isn't your launch.", show_alert=True)
        return
    await cb.answer()

    symbol, name = p["symbol"], p["name"]
    await cb.message.edit_text(f"Preparing <b>${symbol}</b> on "
                               f"{'Pons' if venue == 'pons' else 'pools.trade'}…")

    try:
        addr = await wallet_svc.wallet_address(p["telegram_id"])
        jwt = await auth_svc.get_jwt(p["telegram_id"])
        balance = (await pf.native_balance(p["telegram_id"]))["wei"]

        if venue == "pons":
            logo = None
            if p["image"]:
                logo = await launch.upload_image(jwt, p["image"], f"{symbol}.jpg")
            res = await launch.pons_launch(
                jwt, name=name, symbol=symbol, wallet=addr, logo=logo,
                description=p["description"],
            )
            fee = int(res.get("launchFeeWei") or 0)
        else:
            image_uri = launch.to_square_png_data_uri(p["image"]) if p["image"] else None
            res = await launch.pools_launch(
                jwt, name=name, symbol=symbol, wallet=addr,
                image_data_uri=image_uri, description=p["description"],
            )
            fee = sum(evm.to_int(t.get("value")) for t in res["transactions"])

        # Answer affordability before estimating gas: a node refuses to estimate
        # a transaction the sender can't pay for, and that refusal is not a
        # sentence to show a person.
        if balance < fee:
            await cb.message.edit_text(
                f"Launching costs <b>{_fmt_eth(fee)} ETH</b> plus gas, and this "
                f"wallet holds {_fmt_eth(balance)} ETH.\n\n"
                f"Send ETH to <code>{addr}</code> on Robinhood Chain (or bring it "
                "over with /bridge) and try again."
            )
            return

        prepared = await evm.build_tx_from_provider(addr, res["transactions"][0])
        cost = evm.tx_cost_wei(prepared) + max(fee - evm.to_int(prepared["value"]), 0)
    except (launch.LaunchError, auth_svc.AuthError, evm.EvmError, pf.PortfolioError) as e:
        await cb.message.edit_text(f"⚠️ Couldn't prepare the launch: {e}")
        return

    if balance < cost:
        await cb.message.edit_text(
            f"Launching needs up to <b>{_fmt_eth(cost)} ETH</b> with gas, and you "
            f"have {_fmt_eth(balance)} ETH.\n\nFund <code>{addr}</code> and try again."
        )
        return

    p["res"], p["venue"] = res, venue
    steps = len(res["transactions"])
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Launch", callback_data=f"lnch:ok:{pid}"),
        InlineKeyboardButton(text="Cancel", callback_data=f"lnch:no:{pid}"),
    ]])
    await audit(p["telegram_id"], "launch_prepared", {"symbol": symbol, "venue": venue})
    await cb.message.edit_text(
        f"<b>Launch ${symbol}</b> — {name}\n"
        f"{'Pons · bonding curve' if venue == 'pons' else 'pools.trade · instant pool'}\n\n"
        + (f"Launch fee: {_fmt_eth(fee)} ETH\n" if fee else "")
        + f"Total (max, with gas): <b>{_fmt_eth(cost)} ETH</b>\n"
        + (f"Transactions: {steps}\n" if steps > 1 else "")
        + f"Image: {'attached' if p['image'] else 'none'}\n\n"
        "<i>This is permanent — the token and its details go on-chain.</i>",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("lnch:"))
async def launch_confirm(cb: CallbackQuery) -> None:
    _, action, pid = cb.data.split(":", 2)
    p = _pending.get(pid)
    if not p:
        await cb.answer("This launch expired. Run /launch again.", show_alert=True)
        return
    if cb.from_user.id != p["telegram_id"]:
        await cb.answer("This isn't your launch.", show_alert=True)
        return
    if action == "no":
        _pending.pop(pid, None)
        await cb.answer("Cancelled")
        await cb.message.edit_text("Cancelled — nothing was launched.")
        return
    if action != "ok" or "res" not in p:
        await cb.answer("Pick how it should launch first.", show_alert=True)
        return

    _pending.pop(pid, None)
    await cb.answer()
    await cb.message.edit_text(f"🚀 Launching <b>${p['symbol']}</b>…")

    async def progress(i: int, total: int) -> None:
        if total > 1:
            await cb.message.edit_text(
                f"🚀 Launching <b>${p['symbol']}</b>\nStep {i}/{total}…"
            )

    try:
        w = await wallet_svc.get_or_create_wallet(p["telegram_id"])
        hashes = await launch.execute(
            w["enc_key_ref"], w["address"], p["res"], on_step=progress
        )
    except (launch.LaunchError, evm.EvmError, SignerError) as e:
        log.warning("launch_failed", telegram_id=p["telegram_id"],
                    venue=p.get("venue"), error=str(e))
        await audit(p["telegram_id"], "launch_failed", {"error": str(e)[:200]})
        await cb.message.edit_text(f"❌ Launch failed: {e}")
        return

    tx_hash = hashes[-1]
    # pools.trade predicts the address; Pons doesn't, so read it off the mint.
    token = p["res"].get("predictedTokenAddress") or await launch.token_address_from_receipt(tx_hash)
    await audit(p["telegram_id"], "launch_confirmed",
                {"symbol": p["symbol"], "venue": p.get("venue"), "hash": tx_hash, "token": token})

    link = f'<a href="{EXPLORER}{tx_hash}">{tx_hash[:10]}…</a>'
    if token:
        await cb.message.edit_text(
            f"✅ <b>${p['symbol']}</b> is live — {p['name']}\n\n"
            f"<code>{token}</code>\n"
            f'<a href="{TOKEN_EXPLORER}{token}">View token</a> · {link}\n\n'
            f"Trade it: <code>/swap 0.01 ETH {p['symbol']}</code>"
        )
    else:
        await cb.message.edit_text(
            f"✅ <b>${p['symbol']}</b> launched.\n{link}\n\n"
            "<i>The token address will show on the explorer in a moment.</i>"
        )
