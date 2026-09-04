"""/wallet — show the user's Robinhood custodial wallet, creating it on first use.

Import (/wallet import <secret>) is DM-only: a secret must never be posted in a
group. In groups we refuse and point the user to DM.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.db import audit, upsert_tg_user
from app.handlers.privacy import private_answer
from app.logging_config import log
from app.services import wallet as wallet_svc
from app.signer_client import SignerError

router = Router(name="wallet")


def _fmt(address: str) -> str:
    return (
        "<b>Your OPRAI wallet</b> · Robinhood Chain\n\n"
        f"<code>{address}</code>\n\n"
        "<i>Custodial &amp; recoverable. This is the same address on every EVM "
        "chain — send ETH here on Robinhood Chain to start, or send it on Base "
        "or Ethereum and bring it over with /bridge.</i>"
    )


@router.message(Command("wallet"))
async def wallet_cmd(message: Message, command: CommandObject) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    args = (command.args or "").split()

    # /wallet import <secret>  — DM only
    if args and args[0].lower() == "import":
        if message.chat.type != ChatType.PRIVATE:
            await message.reply(
                "🔒 Import only works in a private chat with me — never paste a "
                "secret in a group. DM me: /wallet import <secret>"
            )
            return
        if len(args) < 2:
            await message.answer("Usage: <code>/wallet import &lt;private-key&gt;</code>")
            return
        try:
            row = await wallet_svc.import_wallet(user.id, args[1])
        except (SignerError, ValueError) as e:
            await message.answer(f"Import failed: {e}")
            return
        await audit(user.id, "wallet_import", {})
        await private_answer(
            message,
            f"✅ Imported your Robinhood wallet:\n<code>{row['address']}</code>\n\n"
            "⚠️ Delete your previous message so the secret isn't left in this chat."
        )
        return

    # Default: ensure + show the wallet.
    try:
        address = await wallet_svc.wallet_address(user.id)
    except SignerError as e:
        log.warning("wallet_create_failed", telegram_id=user.id, error=str(e))
        await message.answer(
            "⚠️ Wallet service is temporarily unavailable. Please try again shortly."
        )
        return
    await audit(user.id, "wallet_show", {})
    # A wallet address in a group ties someone's Telegram identity to an
    # on-chain one, permanently and for everyone reading.
    await private_answer(message, _fmt(address))
