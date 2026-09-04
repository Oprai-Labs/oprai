"""/bridge — bring funds onto Robinhood Chain from another EVM chain.

The custodial wallet is one key, so it is the SAME address everywhere: whatever
a user already holds at their OPRAI address on Base, Arbitrum or Ethereum can
be moved here without a second wallet. Relay does the routing; the origin
transaction is signed on the ORIGIN chain, and a cross-chain fill only counts
as done when Relay reports the intent settled — the origin receipt just means
the money left.
"""

from __future__ import annotations

import asyncio
import secrets
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db import audit, upsert_tg_user
from app.handlers.privacy import private_answer
from app.logging_config import log
from app.services import auth as auth_svc
from app.services import chains
from app.services import evm
from app.services import relay
from app.services import wallet as wallet_svc
from app.signer_client import SignerError

router = Router(name="bridge")

WEI = 10**18
_pending: dict[str, dict] = {}

USAGE = (
    "Usage: <code>/bridge &lt;amount&gt; ETH from &lt;chain&gt;</code>\n"
    "Example: <code>/bridge 0.05 ETH from base</code>\n\n"
    "Chains: " + ", ".join(c.key for c in chains.source_chains())
)


def _fmt_eth(wei: int) -> str:
    return f"{Decimal(wei) / WEI:.6f}".rstrip("0").rstrip(".") or "0"


async def _native_balance(address: str, chain_id: int) -> int:
    res = await evm.rpc("eth_getBalance", [address, "latest"], chain_id)
    return evm.to_int(res)


@router.message(Command("bridge"))
async def bridge_cmd(message: Message, command: CommandObject) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    args = [a for a in (command.args or "").split() if a.lower() != "from"]

    if len(args) < 3 or args[1].upper().lstrip("$") != "ETH":
        await message.answer(USAGE)
        return
    amount_str, chain_ref = args[0], args[2]

    try:
        amount = Decimal(amount_str)
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ArithmeticError):
        await message.answer("That amount doesn't look right.\n\n" + USAGE)
        return

    src = chains.resolve(chain_ref)
    if src is None or src.id == chains.ROBINHOOD:
        await message.answer(
            f"I can't bridge from <b>{chain_ref}</b>.\n\n" + USAGE
        )
        return

    addr = await wallet_svc.wallet_address(user.id)
    value = int(amount * WEI)

    try:
        have = await _native_balance(addr, src.id)
    except (evm.EvmError, KeyError) as e:
        await private_answer(message, f"⚠️ Couldn't read your {src.name} balance: {e}")
        return

    if have < value:
        await message.answer(
            f"You hold <b>{_fmt_eth(have)} ETH</b> on {src.name}, so {amount_str} "
            f"isn't there to bridge.\n\nYour address is the same on every chain — "
            f"send ETH to <code>{addr}</code> on {src.name} first."
        )
        return

    await private_answer(message, f"Pricing {amount_str} ETH from {src.name} → Robinhood Chain…")
    try:
        jwt = await auth_svc.get_jwt(user.id)
        params = relay.build_params(
            origin_currency=relay.NATIVE,
            destination_currency=relay.NATIVE,
            amount=str(amount),
            origin_chain_id=src.id,
            destination_chain_id=chains.ROBINHOOD,
            sender=addr,
            recipient=addr,
        )
        q = await relay.quote(jwt, params)
    except (relay.RelayError, auth_svc.AuthError) as e:
        await private_answer(message, f"⚠️ No bridge route right now: {e}")
        return

    s = relay.summarize(q)
    pid = secrets.token_urlsafe(8)
    _pending[pid] = {
        "telegram_id": user.id, "params": params,
        "amount": amount_str, "src": src.name, "src_id": src.id,
    }
    eta = f"\nETA: ~{s['eta_s']}s" if s.get("eta_s") else ""
    usd = f" (~${s['out']['usd']})" if s["out"].get("usd") else ""
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Confirm bridge", callback_data=f"br:ok:{pid}"),
        InlineKeyboardButton(text="Cancel", callback_data=f"br:no:{pid}"),
    ]])
    await audit(user.id, "bridge_quoted", {"from": src.key, "amount": amount_str})
    await private_answer(
        message,
        f"<b>Bridge {amount_str} ETH</b>\n{src.name} → Robinhood Chain\n\n"
        f"You receive: <b>{s['out']['amount']} {s['out']['symbol']}</b>{usd}{eta}\n\n"
        "<i>Signed on " + src.name + ", filled on Robinhood. Quote includes "
        "OPRAI's fee.</i>",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("br:"))
async def bridge_confirm(cb: CallbackQuery) -> None:
    _, action, pid = cb.data.split(":", 2)
    p = _pending.get(pid)
    if not p:
        await cb.answer("This quote expired. Run /bridge again.", show_alert=True)
        return
    if cb.from_user.id != p["telegram_id"]:
        await cb.answer("This isn't your bridge.", show_alert=True)
        return
    if action == "no":
        _pending.pop(pid, None)
        await cb.answer("Cancelled")
        await cb.message.edit_text("Cancelled — nothing was bridged.")
        return

    _pending.pop(pid, None)
    await cb.answer()
    await cb.message.edit_text(f"Bridging {p['amount']} ETH from {p['src']}…")

    async def progress(i: int, total: int, kind: str) -> None:
        await cb.message.edit_text(
            f"⏳ Bridging {p['amount']} ETH from {p['src']}\nStep {i}/{total}: {kind}…"
        )

    try:
        jwt = await auth_svc.get_jwt(p["telegram_id"])
        w = await wallet_svc.get_or_create_wallet(p["telegram_id"])
        steps, request_id, _ = await relay.build(jwt, p["params"])
        # Each step carries its own chainId, so the origin transaction is signed
        # and broadcast on the origin chain, not here.
        hashes = await relay.execute_steps(
            w["enc_key_ref"], w["address"], steps, on_step=progress
        )
    except (relay.RelayError, auth_svc.AuthError, evm.EvmError, SignerError) as e:
        log.warning("bridge_failed", telegram_id=p["telegram_id"], error=str(e))
        await audit(p["telegram_id"], "bridge_failed", {"error": str(e)[:200]})
        await cb.message.edit_text(f"❌ Bridge failed: {e}")
        return

    await audit(p["telegram_id"], "bridge_submitted",
                {"hashes": hashes, "requestId": request_id})
    await cb.message.edit_text(
        f"⏳ Sent from {p['src']} — waiting for it to land on Robinhood Chain…"
    )

    # The origin receipt only means the money left. A cross-chain fill is done
    # when Relay says the intent settled.
    if not request_id:
        await cb.message.edit_text(
            f"⏳ Sent {p['amount']} ETH from {p['src']}. It should arrive shortly."
        )
        return

    final = None
    for _ in range(40):  # ~2 minutes
        try:
            st = (await relay.intent_status(jwt, request_id)).get("status")
        except relay.RelayError:
            st = None
        if st in ("success", "failure", "refunded"):
            final = st
            break
        await asyncio.sleep(3)

    if final == "success":
        await audit(p["telegram_id"], "bridge_confirmed", {"requestId": request_id})
        await relay.record(jwt, request_id)
        await cb.message.edit_text(
            f"✅ <b>{p['amount']} ETH</b> arrived on Robinhood Chain.\n"
            "Check it with /balance."
        )
    elif final == "refunded":
        await cb.message.edit_text(
            "↩️ The bridge couldn't fill and was refunded on the origin chain."
        )
    elif final == "failure":
        await cb.message.edit_text("❌ The bridge failed. Funds stay on the origin chain.")
    else:
        await cb.message.edit_text(
            f"⏳ Still settling. {p['amount']} ETH left {p['src']} — it usually lands "
            "within a couple of minutes. /balance will show it."
        )
