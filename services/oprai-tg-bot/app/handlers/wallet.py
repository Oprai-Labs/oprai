"""/wallet — show the user's custodial wallets, creating them on first use.

Import (/wallet import <chain> <secret>) is DM-only: a secret must never be
posted in a group. In groups we refuse and point the user to DM.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.db import audit, upsert_tg_user
from app.logging_config import log
from app.services import wallet as wallet_svc
from app.signer_client import SignerError

router = Router(name="wallet")


def _fmt(addresses: dict[str, str]) -> str:
    return (
        "<b>Your OPRAI wallets</b>\n\n"
        f"◎ <b>Solana</b>\n<code>{addresses['solana']}</code>\n\n"
        f"⬡ <b>EVM</b>\n<code>{addresses['evm']}</code>\n\n"
        "<i>Custodial &amp; recoverable. Fund these to start. "
        "Withdrawals to external addresses ask for confirmation.</i>"
    )


@router.message(Command("wallet"))
async def wallet_cmd(message: Message, command: CommandObject) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    args = (command.args or "").split()

    # /wallet import <chain> <secret>  — DM only
    if args and args[0].lower() == "import":
        if message.chat.type != ChatType.PRIVATE:
            await message.reply(
                "🔒 Import only works in a private chat with me — never paste a "
                "secret in a group. DM me: /wallet import <chain> <secret>"
            )
            return
        if len(args) < 3:
            await message.answer("Usage: <code>/wallet import &lt;solana|evm&gt; &lt;secret&gt;</code>")
            return
        chain, secret = args[1].lower(), args[2]
        try:
            row = await wallet_svc.import_wallet(user.id, chain, secret)
        except (SignerError, ValueError) as e:
            await message.answer(f"Import failed: {e}")
            return
        await audit(user.id, "wallet_import", {"chain": chain})
        # Nudge the user to delete the message that carried the secret.
        await message.answer(
            f"✅ Imported {chain} wallet:\n<code>{row['address']}</code>\n\n"
            "⚠️ Delete your previous message so the secret isn't left in this chat."
        )
        return

    # Default: ensure + show both wallets.
    try:
        addresses = await wallet_svc.ensure_all_wallets(user.id)
    except SignerError as e:
        log.warning("wallet_create_failed", telegram_id=user.id, error=str(e))
        await message.answer(
            "⚠️ Wallet service is temporarily unavailable. Please try again shortly."
        )
        return
    await audit(user.id, "wallet_show", {})
    await message.answer(_fmt(addresses))
