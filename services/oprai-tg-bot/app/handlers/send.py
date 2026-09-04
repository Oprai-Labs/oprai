"""/send — transfer ETH, tokens or tokenized stocks on Robinhood Chain.

Flow: parse -> resolve token + recipient -> check the balance that actually
matters -> build from live chain state -> show what it will really cost -> the
INITIATOR confirms -> sign + submit -> wait for the receipt before calling it
done. A submitted transaction is not a successful one.
"""

from __future__ import annotations

import secrets
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db import audit, pool, upsert_tg_user
from app.handlers.privacy import private_answer
from app.logging_config import log
from app.services import evm
from app.services import portfolio as pf
from app.services import tokens as tok
from app.services import wallet as wallet_svc
from app.signer_client import SignerError

router = Router(name="send")

WEI = 10**18
EXPLORER = "https://robinscan.io/tx/"

# pending confirmations: id -> {telegram_id, tx, display, label, gas_cost}
_pending: dict[str, dict] = {}

USAGE = (
    "Usage: <code>/send &lt;amount&gt; &lt;token&gt; &lt;0xaddress|@username&gt;</code>\n"
    "Examples:\n"
    "• <code>/send 0.01 ETH 0xAbC…</code>\n"
    "• <code>/send 5 NVDA @friend</code>\n"
    "• <code>/send 25 USDG 0xAbC…</code>"
)


def _fmt_units(amount: int, decimals: int) -> str:
    s = f"{Decimal(amount) / (10**decimals):f}".rstrip("0").rstrip(".")
    return s or "0"


def _fmt_eth(wei: int) -> str:
    return _fmt_units(wei, 18)


async def _resolve_recipient(token: str) -> tuple[str | None, str]:
    """-> (address, label). Accepts a 0x address or @username of a bot user."""
    t = token.strip()
    if t.startswith("0x") and len(t) == 42:
        return t, t
    if t.startswith("@"):
        uname = t[1:]
        row = await pool().fetchrow(
            "SELECT w.address FROM tg_wallets w JOIN tg_users u USING (telegram_id) "
            "WHERE lower(u.username) = lower($1) AND w.chain = 'evm'",
            uname,
        )
        if row:
            return row["address"], f"{t} ({row['address'][:10]}…)"
        return None, t
    return None, t


def _confirm_kb(pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Confirm", callback_data=f"send:ok:{pid}"),
            InlineKeyboardButton(text="Cancel", callback_data=f"send:no:{pid}"),
        ]]
    )


@router.message(Command("send"))
async def send_cmd(message: Message, command: CommandObject) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    args = (command.args or "").split()

    if len(args) < 3:
        await message.answer(USAGE)
        return
    amount_str, token_ref, recipient_ref = args[0], args[1], args[2]

    try:
        amount = Decimal(amount_str)
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ArithmeticError):
        await message.answer("That amount doesn't look right.\n\n" + USAGE)
        return

    to, label = await _resolve_recipient(recipient_ref)
    if not to:
        await message.answer(
            f"I couldn't resolve <b>{label}</b>. Give me a 0x address, or a "
            "@username of someone who has already started this bot."
        )
        return

    from_addr = await wallet_svc.wallet_address(user.id)
    is_native = token_ref.upper().lstrip("$") == "ETH"

    try:
        if is_native:
            value_wei = int(amount * WEI)
            tx = await evm.build_transfer(from_addr, to, value_wei)
            spend_display = f"{_fmt_eth(value_wei)} ETH"
            native_needed = evm.tx_cost_wei(tx)
        else:
            matches = await tok.resolve(token_ref)
            if not matches:
                await message.answer(
                    f"I don't know a token called <b>{token_ref}</b> on Robinhood "
                    "Chain. Try its symbol (NVDA, TSLA, USDG) or paste its address."
                )
                return
            exact = [m for m in matches if m["symbol"].upper() == token_ref.upper().lstrip("$")]
            if not exact and len(matches) > 1:
                listing = "\n".join(
                    f"• <b>{m['symbol']}</b> — {m['name'] or 'token'}" for m in matches[:6]
                )
                await message.answer(
                    f"<b>{token_ref}</b> matches several tokens — say which one:\n\n{listing}"
                )
                return
            t = (exact or matches)[0]

            units = int(amount * (10 ** t["decimals"]))
            if units <= 0:
                await message.answer(
                    f"{amount_str} is below {t['symbol']}'s smallest unit "
                    f"({t['decimals']} decimals)."
                )
                return

            held = await tok.token_balance(t["address"], from_addr)
            if held < units:
                await private_answer(
                    message,
                    f"Not enough {t['symbol']}. You hold "
                    f"<b>{_fmt_units(held, t['decimals'])}</b> and tried to send "
                    f"{_fmt_units(units, t['decimals'])}."
                )
                return

            data = evm.encode_erc20_transfer(to, units)
            tx = await evm.build_transfer(from_addr, t["address"], 0, data)
            spend_display = f"{_fmt_units(units, t['decimals'])} {t['symbol']}"
            native_needed = evm.tx_cost_wei(tx)  # value is 0 -> gas only

        balance = (await pf.native_balance(user.id))["wei"]
    except (evm.EvmError, pf.PortfolioError, tok.TokenError) as e:
        await private_answer(message, f"⚠️ Couldn't prepare that transfer: {e}")
        return

    gas_cost = int(tx["gas"]) * int(tx["max_fee_per_gas"])
    if balance < native_needed:
        await private_answer(
            message,
            f"Not enough ETH for {'the transfer' if is_native else 'gas'}. This needs "
            f"up to <b>{_fmt_eth(native_needed)} ETH</b>, your balance is "
            f"{_fmt_eth(balance)} ETH.\n\nFund <code>{from_addr}</code> on "
            "Robinhood Chain and try again."
        )
        return

    pid = secrets.token_urlsafe(8)
    _pending[pid] = {
        "telegram_id": user.id,
        "tx": tx,
        "display": spend_display,
        "label": label,
        "to": to,
    }
    await audit(user.id, "send_prepared", {"what": spend_display, "to": to})
    await private_answer(
        message,
        f"<b>Send {spend_display}</b> · Robinhood Chain\n\n"
        f"To: <code>{label}</code>\n"
        f"Network fee (max): {_fmt_eth(gas_cost)} ETH\n"
        f"ETH after: ~{_fmt_eth(balance - native_needed)} ETH",
        reply_markup=_confirm_kb(pid),
    )


@router.callback_query(F.data.startswith("send:"))
async def send_confirm(cb: CallbackQuery) -> None:
    _, action, pid = cb.data.split(":", 2)
    p = _pending.get(pid)

    if not p:
        await cb.answer("This request expired. Send the command again.", show_alert=True)
        return
    # Only the person who initiated it can act on it — in a group, everyone can
    # see the message but nobody else can move someone else's money.
    if cb.from_user.id != p["telegram_id"]:
        await cb.answer("This isn't your transaction.", show_alert=True)
        return

    if action == "no":
        _pending.pop(pid, None)
        await cb.answer("Cancelled")
        await cb.message.edit_text("Cancelled — nothing was sent.")
        return

    _pending.pop(pid, None)
    await cb.answer()
    await cb.message.edit_text(f"Sending {p['display']} to <code>{p['label']}</code>…")

    try:
        w = await wallet_svc.get_or_create_wallet(p["telegram_id"])
        tx_hash = await evm.sign_and_send(w["enc_key_ref"], p["tx"])
    except (evm.EvmError, SignerError) as e:
        log.warning("send_failed", telegram_id=p["telegram_id"], error=str(e))
        await audit(p["telegram_id"], "send_failed", {"error": str(e)[:200]})
        await cb.message.edit_text(f"❌ Transfer failed: {e}")
        return

    await audit(p["telegram_id"], "send_submitted", {"hash": tx_hash, "to": p["to"]})
    link = f'<a href="{EXPLORER}{tx_hash}">{tx_hash[:10]}…</a>'
    await cb.message.edit_text(f"⏳ Submitted {link} — waiting for confirmation…")

    # A signature is not a success: only a receipt decides.
    receipt = await evm.wait_receipt(tx_hash)
    if receipt is None:
        await cb.message.edit_text(
            f"⏳ Still pending: {link}\nIt will land shortly — check the explorer."
        )
        return
    if evm.receipt_succeeded(receipt):
        await audit(p["telegram_id"], "send_confirmed", {"hash": tx_hash})
        await cb.message.edit_text(
            f"✅ Sent <b>{p['display']}</b> to <code>{p['label']}</code>\n{link}"
        )
    else:
        await audit(p["telegram_id"], "send_reverted", {"hash": tx_hash})
        await cb.message.edit_text(f"❌ The transaction reverted on-chain.\n{link}")
