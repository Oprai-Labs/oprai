"""/launch — create a token on Robinhood Chain (Pons bonding curve).

Reply to a photo with `/launch TICKER Name` and that photo becomes the token's
image: the picture someone just posted is usually the whole idea, and making
people leave chat to host it kills the moment.

The image is uploaded to our own gateway first, because Pons writes the URL
on-chain and it has to stay fetchable afterwards.
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


async def _image_from(message: Message) -> tuple[bytes, str] | None:
    """The replied-to photo, if there is one."""
    src = message.reply_to_message
    photo = (src.photo[-1] if src and src.photo else None) or (
        message.photo[-1] if message.photo else None
    )
    if not photo:
        return None
    buf = await message.bot.download(photo.file_id)
    return buf.read(), f"{photo.file_unique_id}.jpg"


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

    addr = await wallet_svc.wallet_address(user.id)
    await message.answer(f"Preparing <b>{symbol}</b> — {name}…")

    try:
        jwt = await auth_svc.get_jwt(user.id)

        logo = None
        img = await _image_from(message)
        if img:
            logo = await launch.upload_image(jwt, img[0], img[1])

        res = await launch.pons_launch(
            jwt, name=name, symbol=symbol, wallet=addr, logo=logo
        )
        tx0 = (res.get("transactions") or [])[0]
        fee = int(res.get("launchFeeWei") or evm.to_int(tx0.get("value")))
        balance = (await pf.native_balance(user.id))["wei"]

        # Check affordability BEFORE estimating gas: a node refuses to estimate
        # a transaction the sender can't pay for, and that refusal would reach
        # the user as an RPC sentence instead of "you need 0.0005 ETH".
        if balance < fee:
            await message.answer(
                f"Launching costs <b>{_fmt_eth(fee)} ETH</b> plus gas, and this "
                f"wallet holds {_fmt_eth(balance)} ETH.\n\n"
                f"Send some ETH to <code>{addr}</code> on Robinhood Chain "
                "(or bring it over with /bridge) and try again."
            )
            return

        prepared = await evm.build_tx_from_provider(addr, tx0)
        cost = evm.tx_cost_wei(prepared)
    except (launch.LaunchError, auth_svc.AuthError, evm.EvmError, pf.PortfolioError) as e:
        await message.answer(f"⚠️ Couldn't prepare the launch: {e}")
        return

    if balance < cost:
        await message.answer(
            f"Launching costs up to <b>{_fmt_eth(cost)} ETH</b> "
            f"({_fmt_eth(int(res.get('launchFeeWei') or 0))} fee + gas), and you "
            f"have {_fmt_eth(balance)} ETH.\n\nFund <code>{addr}</code> and try again."
        )
        return

    pid = secrets.token_urlsafe(8)
    _pending[pid] = {
        "telegram_id": user.id, "res": res, "symbol": symbol, "name": name,
        "logo": logo,
    }
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Launch", callback_data=f"lnch:ok:{pid}"),
        InlineKeyboardButton(text="Cancel", callback_data=f"lnch:no:{pid}"),
    ]])
    await audit(user.id, "launch_prepared", {"symbol": symbol, "name": name})
    await message.answer(
        f"<b>Launch ${symbol}</b> — {name}\nRobinhood Chain · Pons\n\n"
        f"Launch fee: {_fmt_eth(int(res.get('launchFeeWei') or 0))} ETH\n"
        f"Total (max, with gas): <b>{_fmt_eth(cost)} ETH</b>\n"
        f"Image: {'attached' if logo else 'none'}\n\n"
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
        log.warning("launch_failed", telegram_id=p["telegram_id"], error=str(e))
        await audit(p["telegram_id"], "launch_failed", {"error": str(e)[:200]})
        await cb.message.edit_text(f"❌ Launch failed: {e}")
        return

    tx_hash = hashes[-1]
    token = await launch.token_address_from_receipt(tx_hash)
    await audit(p["telegram_id"], "launch_confirmed",
                {"symbol": p["symbol"], "hash": tx_hash, "token": token})

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
