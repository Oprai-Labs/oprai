"""Claiming a transfer someone sent to your handle before you had a wallet.

The recipient arrives through a link the sender forwarded — Telegram gives us
no other way to reach them. Opening it creates their wallet and runs the
transfer out of the sender's, which is what the sender already confirmed.

Two things this is careful about. The claim is bound to the handle the sender
named, so forwarding the link somewhere else cannot redirect the money. And a
transfer that fails — usually because the sender spent the funds while the
claim was outstanding — releases the claim instead of consuming it, so the
sender can top up and the same link still works.
"""

from __future__ import annotations

from app.db import audit
from app.logging_config import log
from app.services import claims, evm, notify
from app.services import tokens as tok
from app.services import wallet as wallet_svc
from app.signer_client import SignerError

EXPLORER = "https://robinscan.io/tx/"


async def handle_claim(message, token: str) -> bool:
    """Deal with a `/start claim_<token>` deep link.

    Returns True when this was a claim link (handled either way), so the
    caller knows not to fall through to the normal welcome.
    """
    user = message.from_user
    try:
        row = await claims.take(token, user.username)
    except claims.ClaimError as e:
        await message.answer(f"⚠️ {e}")
        return True

    amount = claims.display(int(row["amount_base"]), row["decimals"])
    await message.answer(
        f"🎁 <b>{amount} {row['symbol']}</b> is waiting for you — setting up "
        "your wallet…"
    )

    try:
        recipient = await wallet_svc.wallet_address(user.id)
        sender = await wallet_svc.get_or_create_wallet(row["from_telegram_id"])
        units = int(row["amount_base"])

        if row["token_address"]:
            data = evm.encode_erc20_transfer(recipient, units)
            tx = await evm.build_transfer(
                sender["address"], row["token_address"], 0, data
            )
        else:
            tx = await evm.build_transfer(sender["address"], recipient, units)

        tx_hash = await evm.send_and_confirm(
            sender["enc_key_ref"], tx, "claimed transfer"
        )
    except (evm.EvmError, SignerError, tok.TokenError) as e:
        # Almost always: the sender spent it while the claim was outstanding.
        # Release the claim so topping up and re-opening the link still works.
        await claims.mark_failed(token, str(e)[:200])
        log.warning("claim_failed", token=token[:8], error=str(e)[:200])
        await audit(user.id, "claim_failed", {"error": str(e)[:200]})
        await message.answer(
            f"❌ I couldn't complete the transfer — the sender may no longer "
            f"have the {row['symbol']}.\n\nYour wallet is ready either way: "
            "/wallet"
        )
        await notify.send(
            message.bot, row["from_telegram_id"],
            f"⚠️ @{row['to_username']} tried to claim your {amount} "
            f"{row['symbol']}, but the transfer failed — check you still hold "
            "it, then send them the link again.",
        )
        return True

    await claims.record_sent(token, tx_hash)
    await audit(user.id, "claim_completed", {"hash": tx_hash})
    link = f'<a href="{EXPLORER}{tx_hash}">{tx_hash[:10]}…</a>'
    await message.answer(
        f"✅ <b>{amount} {row['symbol']}</b> is yours.\n{link}\n\n"
        "It's in your OPRAI wallet — /portfolio to see it, /swap to trade it."
    )
    await notify.send(
        message.bot, row["from_telegram_id"],
        f"✅ @{row['to_username']} claimed your {amount} {row['symbol']}.\n{link}",
    )
    return True


async def offer_pending(message) -> bool:
    """Tell someone what is waiting for them, when they arrive on their own.

    A claim link is how the sender reaches a stranger, but the recipient often
    just opens the bot — and then the transfer sat there unseen, because
    nothing looked for it. Once they have started us we can speak first, so
    this runs on /start.

    Returns whether anything was waiting.
    """
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    username = message.from_user.username
    if not username:
        return False
    waiting = await claims.pending_for(username)
    if not waiting:
        return False

    for row in waiting[:5]:
        amount = claims.display(int(row["amount_base"]), row["decimals"])
        await message.answer(
            f"🎁 <b>{amount} {row['symbol']}</b> is waiting for you.\n\n"
            "<i>Someone sent it before you had a wallet. Tap to collect it.</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=f"Claim {amount} {row['symbol']}",
                                     callback_data=f"clm:{row['token']}"),
            ]]),
        )
    return True


def register(router) -> None:
    """Wire the claim button onto a router that is already registered."""
    from aiogram import F
    from aiogram.types import CallbackQuery

    @router.callback_query(F.data.startswith("clm:"))
    async def claim_button(cb: CallbackQuery) -> None:
        from app.handlers.home import as_person

        await cb.answer()
        token = cb.data.split(":", 1)[1]
        await handle_claim(as_person(cb), token)
