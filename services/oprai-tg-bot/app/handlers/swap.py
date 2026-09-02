"""/swap — trade on Robinhood Chain.

Two venues, picked by what is being traded:
  • tokenized stocks (NVDA, TSLA, …) live in Uniswap V3 pools — Relay doesn't
    route them at all,
  • ETH / WETH / USDG go through Relay, which is how OPRAI swaps on EVM.
Either way OPRAI's commission is applied server-side in the quote, the bot signs
with the isolated signer, and nothing counts as done until a receipt says so.
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
from app.services import uniswap
from app.services import wallet as wallet_svc
from app.signer_client import SignerError

router = Router(name="swap")

EXPLORER = "https://robinscan.io/tx/"
_pending: dict[str, dict] = {}

USAGE = (
    "Usage: <code>/swap &lt;amount&gt; &lt;from&gt; &lt;to&gt;</code>\n"
    "Examples:\n"
    "• <code>/swap 0.01 ETH NVDA</code> — buy a stock\n"
    "• <code>/swap 5 NVDA USDG</code> — sell one\n"
    "• <code>/swap 0.01 ETH USDG</code>"
)


def _fmt_units(amount: int, decimals: int) -> str:
    return f"{Decimal(amount) / (10**decimals):f}".rstrip("0").rstrip(".") or "0"


async def _resolve_side(ref: str) -> tuple[str, str, int, bool] | None:
    """-> (currency_address, symbol, decimals, is_stock). ETH -> zero address."""
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

    # Tokenized stocks trade on Uniswap; Relay doesn't list them.
    venue = "uniswap" if (src_stock or dst_stock) else "relay"

    # Refuse before quoting if the wallet plainly can't fund it — a card the
    # user can't complete is our failure, not theirs.
    try:
        native = (await pf.native_balance(user.id))["wei"]
        have = native if src_addr == relay.NATIVE else await tok.token_balance(src_addr, addr)
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
    if native == 0:
        await message.answer(
            "You have no ETH on Robinhood Chain, so there's nothing to pay gas "
            f"with.\n\nFund <code>{addr}</code> and try again."
        )
        return

    await message.answer(f"Getting the best route for {amount_str} {src_sym} → {dst_sym}…")
    try:
        jwt = await auth_svc.get_jwt(user.id)
        if venue == "uniswap":
            params = uniswap.build_params(
                origin_currency=src_addr,
                destination_currency=dst_addr,
                amount=str(amount),
                sender=addr,
            )
            q = await uniswap.quote(jwt, params)
            s = uniswap.summarize(q)
            out_amount, out_symbol = s["out_amount"], dst_sym
            extra = ""
            if s["impact"] is not None:
                extra += f"\nPrice impact: {s['impact']}%"
            if s["gas_usd"]:
                extra += f"\nEstimated gas: ${float(s['gas_usd']):.4f}"
            steps = uniswap.transaction_count(q)
            if steps > 1 or s["needs_permit"]:
                extra += f"\n<i>{steps} transaction(s)"
                extra += " + a permit signature" if s["needs_permit"] else ""
                extra += " — I'll walk through them.</i>"
        else:
            params = relay.build_params(
                origin_currency=src_addr,
                destination_currency=dst_addr,
                amount=str(amount),
                sender=addr,
                recipient=addr,
            )
            rq = await relay.quote(jwt, params)
            rs = relay.summarize(rq)
            out_amount, out_symbol = rs["out"]["amount"], rs["out"]["symbol"]
            extra = ""
            if rs["out"].get("usd"):
                extra += f"\nValue: ~${rs['out']['usd']}"
            if rs.get("eta_s"):
                extra += f"\nETA: ~{rs['eta_s']}s"
    except (relay.RelayError, uniswap.UniswapError, auth_svc.AuthError) as e:
        await message.answer(f"⚠️ No route right now: {e}")
        return

    pid = secrets.token_urlsafe(8)
    _pending[pid] = {
        "telegram_id": user.id,
        "venue": venue,
        "params": params,
        "src": src_sym,
        "dst": dst_sym,
        "amount": amount_str,
    }
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Confirm swap", callback_data=f"swap:ok:{pid}"),
        InlineKeyboardButton(text="Cancel", callback_data=f"swap:no:{pid}"),
    ]])
    await audit(user.id, "swap_quoted",
                {"venue": venue, "from": src_sym, "to": dst_sym, "amount": amount_str})
    await message.answer(
        f"<b>Swap {amount_str} {src_sym} → {dst_sym}</b> · Robinhood Chain\n\n"
        f"You receive: <b>{out_amount} {out_symbol}</b>{extra}\n\n"
        f"<i>via {'Uniswap' if venue == 'uniswap' else 'Relay'} · quote includes "
        "OPRAI's fee. Rates move — confirm to execute.</i>",
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

    async def progress(i: int, total: int, kind: str) -> None:
        await cb.message.edit_text(
            f"⏳ {p['amount']} {p['src']} → {p['dst']}\nStep {i}/{total}: {kind}…"
        )

    request_id = None
    try:
        jwt = await auth_svc.get_jwt(p["telegram_id"])
        w = await wallet_svc.get_or_create_wallet(p["telegram_id"])
        # Re-price at execution time: the earlier quote was indicative, and a
        # permit carries a deadline.
        if p["venue"] == "uniswap":
            fresh = await uniswap.quote(jwt, p["params"])
            hashes = await uniswap.execute(
                jwt, w["enc_key_ref"], w["address"], fresh, on_step=progress
            )
            out = f"{fresh.get('outputAmountDisplay')} {p['dst']}"
        else:
            steps, request_id, rq = await relay.build(jwt, p["params"])
            hashes = await relay.execute_steps(
                w["enc_key_ref"], w["address"], steps, on_step=progress
            )
            out = f"{relay.summarize(rq)['out']['amount']} {p['dst']}"
    except (relay.RelayError, uniswap.UniswapError, auth_svc.AuthError,
            evm.EvmError, SignerError) as e:
        log.warning("swap_failed", telegram_id=p["telegram_id"], venue=p["venue"], error=str(e))
        await audit(p["telegram_id"], "swap_failed", {"venue": p["venue"], "error": str(e)[:200]})
        await cb.message.edit_text(f"❌ Swap failed: {e}")
        return

    if request_id:
        await relay.record(jwt, request_id)  # book volume/tier; never fatal
    await audit(p["telegram_id"], "swap_confirmed", {"venue": p["venue"], "hashes": hashes})

    last = hashes[-1]
    link = f'<a href="{EXPLORER}{last}">{last[:10]}…</a>'
    await cb.message.edit_text(
        f"✅ Swapped <b>{p['amount']} {p['src']}</b> → <b>{out}</b>\n{link}"
        + (f"\n<i>{len(hashes)} transactions</i>" if len(hashes) > 1 else "")
    )
