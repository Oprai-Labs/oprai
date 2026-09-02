"""/swap — trade on Robinhood Chain through Relay.

Relay is how OPRAI swaps on EVM, and OPRAI's commission is applied server-side
in the quote, so what the card shows is what the user gets. An ERC-20 input
needs an approval first; Relay returns that as an extra step and we execute
every step in order, confirming each before the next.
"""

from __future__ import annotations

import secrets
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db import audit, upsert_tg_user
from app.logging_config import log
from app.services import auth as auth_svc
from app.services import evm
from app.services import portfolio as pf
from app.services import relay
from app.services import tokens as tok
from app.services import wallet as wallet_svc
from app.signer_client import SignerError

router = Router(name="swap")

EXPLORER = "https://robinscan.io/tx/"
_pending: dict[str, dict] = {}

USAGE = (
    "Usage: <code>/swap &lt;amount&gt; &lt;from&gt; &lt;to&gt;</code>\n"
    "Examples:\n"
    "• <code>/swap 0.01 ETH USDG</code>\n"
    "• <code>/swap 25 USDG WETH</code>"
)


def _fmt_units(amount: int, decimals: int) -> str:
    return f"{Decimal(amount) / (10**decimals):f}".rstrip("0").rstrip(".") or "0"


async def _resolve_side(ref: str) -> tuple[str, str, int, bool] | None:
    """-> (relay_currency, symbol, decimals, is_stock). ETH -> zero-address."""
    r = ref.strip().lstrip("$")
    if r.upper() == "ETH":
        return relay.NATIVE, "ETH", 18, False
    matches = await tok.resolve(r)
    if not matches:
        return None
    exact = [m for m in matches if m["symbol"].upper() == r.upper()]
    m = (exact or matches)[0]
    return m["address"], m["symbol"], m["decimals"], bool(m.get("is_stock"))


@router.message(Command("swap"))
async def swap_cmd(message: Message, command: CommandObject) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    args = (command.args or "").split()
    if len(args) < 3:
        await message.answer(USAGE)
        return

    amount_str, from_ref, to_ref = args[0], args[1], args[2]
    try:
        amount = Decimal(amount_str)
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ArithmeticError):
        await message.answer("That amount doesn't look right.\n\n" + USAGE)
        return

    src = await _resolve_side(from_ref)
    dst = await _resolve_side(to_ref)
    if not src:
        await message.answer(f"I don't know a token called <b>{from_ref}</b> on Robinhood Chain.")
        return
    if not dst:
        await message.answer(f"I don't know a token called <b>{to_ref}</b> on Robinhood Chain.")
        return
    if src[0].lower() == dst[0].lower():
        await message.answer("That's the same token on both sides.")
        return

    src_addr, src_sym, src_dec, src_stock = src
    dst_addr, dst_sym, _, dst_stock = dst
    addr = await wallet_svc.wallet_address(user.id)

    # Refuse before quoting if the wallet plainly can't fund the input — a card
    # the user can't complete is our failure, not theirs.
    try:
        if src_addr == relay.NATIVE:
            have = (await pf.native_balance(user.id))["wei"]
        else:
            have = await tok.token_balance(src_addr, addr)
    except (pf.PortfolioError, evm.EvmError) as e:
        await message.answer(f"⚠️ Couldn't read your balance: {e}")
        return

    want = int(amount * (10**src_dec))
    if have < want:
        await message.answer(
            f"Not enough {src_sym}. You hold <b>{_fmt_units(have, src_dec)}</b> "
            f"and tried to swap {amount_str}."
        )
        return

    await message.answer(f"Getting the best route for {amount_str} {src_sym} → {dst_sym}…")
    try:
        jwt = await auth_svc.get_jwt(user.id)
        params = relay.build_params(
            origin_currency=src_addr,
            destination_currency=dst_addr,
            amount=str(amount),
            sender=addr,
            recipient=addr,
        )
        q = await relay.quote(jwt, params)
    except (relay.RelayError, auth_svc.AuthError) as e:
        if src_stock or dst_stock:
            # Relay lists liquid assets, not Robinhood's tokenized stocks.
            stock = src_sym if src_stock else dst_sym
            await message.answer(
                f"Relay doesn't route <b>{stock}</b> — tokenized stocks trade on "
                "Robinhood's own venues. Stock swaps are coming next; for now you "
                "can swap ETH, WETH and USDG here, and /send any stock you hold."
            )
            return
        await message.answer(f"⚠️ No route right now: {e}")
        return

    s = relay.summarize(q)
    pid = secrets.token_urlsafe(8)
    _pending[pid] = {"telegram_id": user.id, "params": params, "src": src_sym, "dst": dst_sym,
                     "amount": amount_str}

    usd = f" (~${s['out']['usd']})" if s["out"].get("usd") else ""
    eta = f"\nETA: ~{s['eta_s']}s" if s.get("eta_s") else ""
    impact = f"\nPrice impact: {s['impact']}%" if s.get("impact") else ""
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Confirm swap", callback_data=f"swap:ok:{pid}"),
        InlineKeyboardButton(text="Cancel", callback_data=f"swap:no:{pid}"),
    ]])
    await audit(user.id, "swap_quoted", {"from": src_sym, "to": dst_sym, "amount": amount_str})
    await message.answer(
        f"<b>Swap {amount_str} {src_sym} → {dst_sym}</b> · Robinhood Chain\n\n"
        f"You receive: <b>{s['out']['amount']} {s['out']['symbol']}</b>{usd}"
        f"{impact}{eta}\n\n"
        "<i>Quote includes OPRAI's fee. Rates move — confirm to execute.</i>",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("swap:"))
async def swap_confirm(cb: CallbackQuery) -> None:
    _, action, pid = cb.data.split(":", 2)
    p = _pending.get(pid)
    if not p:
        await cb.answer("This quote expired. Run /swap again.", show_alert=True)
        return
    if cb.from_user.id != p["telegram_id"]:
        await cb.answer("This isn't your swap.", show_alert=True)
        return
    if action == "no":
        _pending.pop(pid, None)
        await cb.answer("Cancelled")
        await cb.message.edit_text("Cancelled — nothing was swapped.")
        return

    _pending.pop(pid, None)
    await cb.answer()
    await cb.message.edit_text(f"Preparing {p['amount']} {p['src']} → {p['dst']}…")

    try:
        jwt = await auth_svc.get_jwt(p["telegram_id"])
        # Re-quote at execution time: the earlier price was indicative.
        steps, request_id, q = await relay.build(jwt, p["params"])
        w = await wallet_svc.get_or_create_wallet(p["telegram_id"])

        async def progress(i: int, total: int, kind: str) -> None:
            await cb.message.edit_text(
                f"⏳ {p['amount']} {p['src']} → {p['dst']}\nStep {i}/{total}: {kind}…"
            )

        hashes = await relay.execute_steps(
            w["enc_key_ref"], w["address"], steps, on_step=progress
        )
    except (relay.RelayError, auth_svc.AuthError, evm.EvmError, SignerError) as e:
        log.warning("swap_failed", telegram_id=p["telegram_id"], error=str(e))
        await audit(p["telegram_id"], "swap_failed", {"error": str(e)[:200]})
        await cb.message.edit_text(f"❌ Swap failed: {e}")
        return

    last = hashes[-1]
    link = f'<a href="{EXPLORER}{last}">{last[:10]}…</a>'
    if request_id:
        await relay.record(jwt, request_id)  # book volume/tier; never fatal
    await audit(p["telegram_id"], "swap_confirmed", {"hashes": hashes, "requestId": request_id})

    out = relay.summarize(q)["out"]
    await cb.message.edit_text(
        f"✅ Swapped <b>{p['amount']} {p['src']}</b> → "
        f"<b>{out['amount']} {out['symbol']}</b>\n{link}"
        + (f"\n<i>{len(hashes)} transactions</i>" if len(hashes) > 1 else "")
    )
