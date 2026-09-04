"""/wallet — the user's custodial wallet, and the ways in and out of it.

    /wallet            show it (created on first use)
    /wallet new        generate a fresh one; the old one is archived, not lost
    /wallet import     bring your own key in
    /wallet export     take your key out
    /wallet list       every wallet you have had here

Custody you cannot leave is a trap, so export exists — but it is the one
command that hands out key material, so it is DM-only, confirmed, and the
message deletes itself. Import is DM-only for the same reason: a secret must
never be posted in a group.

Nothing here ever replaces a wallet in place. A new or imported wallet archives
the previous one, which keeps its key and stays exportable — otherwise anything
left at the old address would become unreachable the moment someone switched.
"""

from __future__ import annotations

import asyncio
import secrets

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from app.db import audit, upsert_tg_user
from app.handlers.home import as_person
from app.handlers.privacy import private_answer
from app.logging_config import log
from app.services import wallet as wallet_svc
from app.signer_client import SignerError

router = Router(name="wallet")


# How long the key stays on screen before the bot deletes its own message.
# Telegram keeps chat history on its servers, so the shorter this is the
# smaller the window in which a compromised account finds it.
EXPORT_VISIBLE_SECONDS = 90

_pending_export: dict[str, dict] = {}


def _wallet_kb() -> InlineKeyboardMarkup:
    """The things you can do to a wallet, on the wallet.

    These existed as `/wallet export` and friends and nobody found them — a
    subcommand you have to already know about is a feature that isn't there.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Export key", callback_data="wal:export"),
         InlineKeyboardButton(text="📥 Import a wallet", callback_data="wal:import")],
        [InlineKeyboardButton(text="🆕 New wallet", callback_data="wal:new"),
         InlineKeyboardButton(text="📋 My wallets", callback_data="wal:list")],
    ])


def _export_kb(pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="I understand — show the key",
                             callback_data=f"wex:ok:{pid}"),
        InlineKeyboardButton(text="Cancel", callback_data=f"wex:no:{pid}"),
    ]])


def _fmt(address: str) -> str:
    return (
        "<b>Your OPRAI wallet</b> · Robinhood Chain\n\n"
        f"<code>{address}</code>\n\n"
        "<i>The same address on every EVM chain — send ETH here on Robinhood "
        "Chain to start, or send it on Base or Ethereum and bring it over with "
        "/bridge.</i>\n\n"
        "<i>The key is yours: export it any time and import it into MetaMask "
        "or anywhere else.</i>"
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

    if args and args[0].lower() == "new":
        await _new_wallet(message)
        return
    if args and args[0].lower() in ("export", "backup", "key"):
        await _offer_export(message, args[1] if len(args) > 1 else None)
        return
    if args and args[0].lower() in ("list", "all"):
        await _list_wallets(message)
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
    await private_answer(message, _fmt(address), reply_markup=_wallet_kb())


async def _new_wallet(message: Message) -> None:
    """Generate a fresh wallet and step the old one aside.

    Nothing is destroyed: the previous wallet keeps its key, stays exportable,
    and anything still sitting at that address remains recoverable. Said out
    loud, because "new wallet" reads like "the old one is gone".
    """
    user = message.from_user
    try:
        previous = await wallet_svc.get_wallet(user.id)
        row = await wallet_svc.new_wallet(user.id)
    except SignerError as e:
        log.warning("wallet_new_failed", telegram_id=user.id, error=str(e))
        await private_answer(message, f"⚠️ Couldn't create a wallet just now: {e}")
        return

    await audit(user.id, "wallet_new", {"address": row["address"]})
    note = ""
    if previous:
        note = (
            f"\n\nYour previous wallet <code>{previous['address']}</code> is "
            "archived — it still works, its key is still yours "
            "(<code>/wallet export &lt;address&gt;</code>), and anything left "
            "there is safe. Move funds over yourself if you want them here."
        )
    await private_answer(message, _fmt(row["address"]) + note)


async def _list_wallets(message: Message) -> None:
    user = message.from_user
    rows = await wallet_svc.list_wallets(user.id)
    if not rows:
        await private_answer(message, "You don't have a wallet yet — /wallet creates one.")
        return

    lines = ["<b>Your wallets</b> · Robinhood Chain", ""]
    for r in rows:
        state = "archived" if r["archived_at"] else "<b>in use</b>"
        how = "imported" if r["imported"] else "created here"
        lines.append(f"• <code>{r['address']}</code>\n    {state} · {how}")
    lines += ["", "<i>Every one of these is still yours: "
              "<code>/wallet export &lt;address&gt;</code>.</i>"]
    await private_answer(message, "\n".join(lines))


async def _offer_export(message: Message, address: str | None) -> None:
    """Ask before handing out a key, and only ever in a private chat."""
    user = message.from_user
    if message.chat.type != ChatType.PRIVATE:
        await message.reply(
            "🔒 I'll only show a private key in a private chat. DM me "
            "<code>/wallet export</code>."
        )
        return

    row = await wallet_svc.get_wallet(user.id)
    if row is None and address is None:
        await message.answer("You don't have a wallet yet — /wallet creates one.")
        return

    target = address or row["address"]
    pid = secrets.token_urlsafe(8)
    _pending_export[pid] = {"telegram_id": user.id, "address": address}
    await private_answer(
        message,
        f"<b>Export the key for</b>\n<code>{target}</code>\n\n"
        "This is the whole wallet. Anyone who reads it can spend everything in "
        "it, for ever — there is no revoking a private key.\n\n"
        f"I'll delete my message after {EXPORT_VISIBLE_SECONDS} seconds, but "
        "Telegram keeps history on its own servers until then, so only do this "
        "somewhere you trust and move the key into a real wallet straight away."
        "\n\n<i>You do not need to export to keep using the bot — this is for "
        "taking your funds elsewhere.</i>",
        reply_markup=_export_kb(pid),
    )


@router.callback_query(F.data.startswith("wex:"))
async def export_confirm(cb: CallbackQuery) -> None:
    _, action, pid = cb.data.split(":", 2)
    p = _pending_export.get(pid)
    if not p:
        await cb.answer("This expired. Run /wallet export again.", show_alert=True)
        return
    if cb.from_user.id != p["telegram_id"]:
        await cb.answer("This isn't yours.", show_alert=True)
        return
    if action == "no":
        _pending_export.pop(pid, None)
        await cb.answer("Cancelled")
        await cb.message.edit_text("Cancelled — nothing was shown.")
        return

    _pending_export.pop(pid, None)
    await cb.answer()
    try:
        out = await wallet_svc.export_secret(p["telegram_id"], p["address"])
    except (SignerError, ValueError) as e:
        log.warning("wallet_export_failed", telegram_id=p["telegram_id"], error=str(e))
        await cb.message.edit_text(f"⚠️ Couldn't export that: {e}")
        return

    # Audited without the key: knowing an export happened is what matters, and
    # a log line is exactly where key material must never end up.
    await audit(p["telegram_id"], "wallet_export", {"address": out["address"]})
    log.warning("wallet_exported", telegram_id=p["telegram_id"], address=out["address"])

    shown = await cb.message.edit_text(
        f"<b>{out['address']}</b>\n\n"
        f"<tg-spoiler><code>{out['secret']}</code></tg-spoiler>\n\n"
        f"<i>Deleting this in {EXPORT_VISIBLE_SECONDS}s. Import it into "
        "MetaMask or any EVM wallet.</i>"
    )
    asyncio.create_task(_erase_later(shown))


async def _erase_later(msg) -> None:
    """Take the key off the screen without waiting on the user.

    Best effort: if the delete fails the key stays visible, so the message
    itself says so rather than promising something we can't guarantee.
    """
    await asyncio.sleep(EXPORT_VISIBLE_SECONDS)
    try:
        await msg.edit_text(
            "🔒 Key hidden. Run <code>/wallet export</code> again if you need it."
        )
    except Exception as e:  # noqa: BLE001 — a failed edit must not raise into the loop
        log.warning("wallet_export_erase_failed", error=str(e))


@router.callback_query(F.data.startswith("wal:"))
async def wallet_button(cb: CallbackQuery) -> None:
    """The wallet card's own buttons.

    A callback's message was sent by us, so `from_user` is the bot — repointed
    here (as a copy; the model is frozen), or every one of these would act on
    the bot's own wallet.
    """
    what = cb.data.split(":", 1)[1]
    await cb.answer()
    message = as_person(cb)

    if what == "export":
        await _offer_export(message, None)
    elif what == "list":
        await _list_wallets(message)
    elif what == "import":
        await message.answer(
            "📥 <b>Import a wallet</b>\n\n"
            "Send me:\n<code>/wallet import 0xYOUR_PRIVATE_KEY</code>\n\n"
            "<i>Only here, never in a group. Your current wallet is archived "
            "rather than replaced, so nothing in it is lost — and delete your "
            "message afterwards, since Telegram keeps it otherwise.</i>"
        )
    elif what == "new":
        await message.answer(
            "🆕 <b>A fresh wallet?</b>\n\n"
            "Your current one is archived, not deleted: its key stays yours, "
            "it stays exportable, and anything in it is safe where it is — but "
            "funds don't move by themselves.\n\n"
            "Send <code>/wallet new</code> to go ahead.",
        )
