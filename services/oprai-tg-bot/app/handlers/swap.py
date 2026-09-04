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
from app.handlers.privacy import private_answer
from app.logging_config import log
from app.services import auth as auth_svc
from app.services import evm
from app.services import portfolio as pf
from app.services import relay
from app.services import tokens as tok
from app.services import sushi, uniswap
from app.services import wallet as wallet_svc
from app.signer_client import SignerError

router = Router(name="swap")

EXPLORER = "https://robinscan.io/tx/"
_pending: dict[str, dict] = {}

USAGE = (
    "Usage: <code>/swap &lt;amount&gt; &lt;from&gt; &lt;to&gt; [on &lt;venue&gt;]</code>\n"
    "Examples:\n"
    "• <code>/swap 0.01 ETH NVDA</code> — buy a stock\n"
    "• <code>/swap 5 NVDA USDG</code> — sell one\n"
    "• <code>/swap 0.01 ETH USDG on sushi</code> — pick the venue\n\n"
    "<i>Without one I quote SushiSwap and Relay and take the better fill. "
    "Tokenized stocks trade on Uniswap.</i>"
)


_VENUE_NAMES = {"uniswap": "Uniswap", "relay": "Relay", "sushi": "SushiSwap"}


def _fmt_units(amount: int, decimals: int) -> str:
    return f"{Decimal(amount) / (10**decimals):f}".rstrip("0").rstrip(".") or "0"


# What someone can call a venue. The model uses the ids; people use the names.
_VENUE_WORDS = {
    "sushi": "sushi", "sushiswap": "sushi",
    "relay": "relay",
    "uniswap": "uniswap", "uni": "uniswap",
}


def _named_venue(rest: list[str]) -> str | None:
    """A venue named after the pair — "on sushi", "via relay", or just the
    name. Returns None when nothing recognisable was said."""
    for word in rest:
        key = word.strip().lower().lstrip("@")
        if key in _VENUE_WORDS:
            return _VENUE_WORDS[key]
    return None


def _to_float(value) -> float | None:
    """Quote amounts arrive as strings, numbers, or with thousands separators
    depending on the venue. A comparison that can't read one of them would
    silently hand the trade to the other."""
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


async def _best_same_chain_route(jwt, wallet, src_addr, dst_addr, dst_sym,
                                 amount, dst_decimals, forced: str | None = None):
    """Quote Relay and Sushi, return whichever fills better — unless a venue
    was named, in which case that one is used even if it fills worse. Someone
    who asks for Sushi is asking for Sushi.

    -> (venue, params, out_amount, out_symbol, extra, built)

    Both are asked at once because the user is waiting, and a venue that fails
    simply loses — one route being down is not a reason to refuse a trade the
    other can do.
    """
    import asyncio

    relay_params = relay.build_params(
        origin_currency=src_addr, destination_currency=dst_addr,
        amount=str(amount), sender=wallet, recipient=wallet,
    )
    sushi_params = {
        "token_in": src_addr, "token_out": dst_addr, "amount": float(amount),
    }

    relay_res, sushi_res = await asyncio.gather(
        relay.quote(jwt, relay_params),
        sushi.swap(jwt, wallet=wallet, **sushi_params),
        return_exceptions=True,
    )

    relay_out = None
    if not isinstance(relay_res, Exception):
        relay_summary = relay.summarize(relay_res)
        relay_out = _to_float(relay_summary["out"]["amount"])

    sushi_out = None
    if not isinstance(sushi_res, Exception):
        raw = _to_float(sushi.summarize(sushi_res)["out_amount"])
        # Sushi answers in base units; Relay in display units. Comparing them
        # without this is comparing 23997043 with 23.94.
        sushi_out = raw / (10**dst_decimals) if raw is not None else None

    if sushi_out is None and relay_out is None:
        raise sushi.SushiError("no route for that pair right now")
    if forced == "sushi" and sushi_out is None:
        raise sushi.SushiError("SushiSwap has no route for that pair right now")
    if forced == "relay" and relay_out is None:
        raise relay.RelayError("Relay has no route for that pair right now")

    take_sushi = (
        forced == "sushi"
        or (forced != "relay"
            and (relay_out is None
                 or (sushi_out is not None and sushi_out > relay_out)))
    )
    if take_sushi:
        extra = ""
        impact = sushi.summarize(sushi_res)["impact"]
        if impact is not None:
            extra += f"\nPrice impact: {float(impact) * 100:.3f}%"
        if relay_out is not None:
            extra += f"\n<i>Better than Relay's {relay_out:,.6f}.</i>".replace(
                ".000000", ""
            )
        steps = sushi.transaction_count(sushi_res)
        if steps > 1:
            extra += f"\n<i>{steps} transactions — I'll walk through them.</i>"
        return ("sushi", sushi_params, f"{sushi_out:,.6f}".rstrip("0").rstrip("."),
                dst_sym, extra, sushi_res)

    summary = relay.summarize(relay_res)
    extra = ""
    if summary["out"].get("usd"):
        extra += f"\nValue: ~${summary['out']['usd']}"
    if summary.get("eta_s"):
        extra += f"\nETA: ~{summary['eta_s']}s"
    if sushi_out is not None:
        extra += f"\n<i>Better than SushiSwap's {sushi_out:,.6f}.</i>".replace(
            ".000000", ""
        )
    return ("relay", relay_params, summary["out"]["amount"],
            summary["out"]["symbol"], extra, None)


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
    # "…on sushi" / "…via relay". Naming a venue has to mean something: the
    # model emits sushi_swap when someone asks for Sushi, and re-deciding the
    # route afterwards silently overrides what they asked for.
    wanted_venue = _named_venue(args[3:])
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
    dst_addr, dst_sym, dst_dec, dst_stock = dst
    addr = await wallet_svc.wallet_address(user.id)

    # Tokenized stocks trade on Uniswap; Relay doesn't list them.
    venue = "uniswap" if (src_stock or dst_stock) else "relay"
    if wanted_venue and (src_stock or dst_stock) and wanted_venue != "uniswap":
        await message.answer(
            f"{src_sym if src_stock else dst_sym} only trades on Uniswap here — "
            f"{_VENUE_NAMES[wanted_venue]} doesn't list tokenized stocks."
        )
        return

    # Refuse before quoting if the wallet plainly can't fund it — a card the
    # user can't complete is our failure, not theirs.
    try:
        native = (await pf.native_balance(user.id))["wei"]
        have = native if src_addr == relay.NATIVE else await tok.token_balance(src_addr, addr)
    except (pf.PortfolioError, evm.EvmError) as e:
        await private_answer(message, f"⚠️ Couldn't read your balance: {e}")
        return

    want = int(amount * (10**src_dec))
    if have < want:
        await private_answer(
            message,
            f"Not enough {src_sym}. You hold <b>{_fmt_units(have, src_dec)}</b> "
            f"and tried to swap {amount_str}."
        )
        return
    if native == 0:
        await private_answer(
            message,
            "You have no ETH on Robinhood Chain, so there's nothing to pay gas "
            f"with.\n\nFund <code>{addr}</code> and try again."
        )
        return

    await private_answer(message, f"Getting the best route for {amount_str} {src_sym} → {dst_sym}…")
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
            # Two venues can do this trade, and which one is better changes
            # with the pair and the size — Relay routes value between chains,
            # Sushi is the chain's own DEX. Ask both and take the better fill
            # rather than picking a favourite and quietly costing the user the
            # difference.
            venue, params, out_amount, out_symbol, extra, sushi_built = (
                await _best_same_chain_route(
                    jwt, addr, src_addr, dst_addr, dst_sym, amount, dst_dec,
                    forced=wanted_venue,
                )
            )
    except (relay.RelayError, uniswap.UniswapError, sushi.SushiError,
            auth_svc.AuthError) as e:
        await private_answer(message, f"⚠️ No route right now: {e}")
        return

    pid = secrets.token_urlsafe(8)
    _pending[pid] = {
        "telegram_id": user.id,
        "venue": venue,
        "params": params,
        "src": src_sym,
        "dst": dst_sym,
        "amount": amount_str,
        "expected_out": out_amount,
    }
    rows = [[
        InlineKeyboardButton(text="✅ Confirm swap", callback_data=f"swap:ok:{pid}"),
        InlineKeyboardButton(text="Cancel", callback_data=f"swap:no:{pid}"),
    ]]
    # The route is a choice, not a verdict. Both venues were just quoted, so
    # offering the other one costs nothing and answers "why this one?".
    other = {"sushi": "relay", "relay": "sushi"}.get(venue)
    if other and not (src_stock or dst_stock):
        rows.append([InlineKeyboardButton(
            text=f"↔ Use {_VENUE_NAMES[other]} instead",
            callback_data=f"swap:via:{other}:{amount_str}:{src_sym}:{dst_sym}",
        )])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await audit(user.id, "swap_quoted",
                {"venue": venue, "from": src_sym, "to": dst_sym, "amount": amount_str})
    await private_answer(
        message,
        f"<b>Swap {amount_str} {src_sym} → {dst_sym}</b> · Robinhood Chain\n\n"
        f"You receive: <b>{out_amount} {out_symbol}</b>{extra}\n\n"
        f"<i>via {_VENUE_NAMES[venue]} · quote includes "
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
        elif p["venue"] == "sushi":
            # Sushi returns the transactions with the quote, so re-asking is
            # how the numbers stay honest between the card and the block.
            fresh = await sushi.swap(
                jwt, wallet=w["address"], **p["params"]
            )
            hashes = await sushi.execute(
                w["enc_key_ref"], w["address"], fresh,
                on_step=lambda i, t: progress(i, t, "swap"),
            )
            out = f"{p['expected_out']} {p['dst']}"
        else:
            steps, request_id, rq = await relay.build(jwt, p["params"])
            hashes = await relay.execute_steps(
                w["enc_key_ref"], w["address"], steps, on_step=progress
            )
            out = f"{relay.summarize(rq)['out']['amount']} {p['dst']}"
    except (relay.RelayError, uniswap.UniswapError, sushi.SushiError,
            auth_svc.AuthError, evm.EvmError, SignerError) as e:
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


@router.callback_query(F.data.startswith("swap:via:"))
async def swap_switch_venue(cb: CallbackQuery) -> None:
    """Re-quote the same trade on the other venue.

    Not a silent switch: it runs the whole flow again, so the person sees the
    new price and confirms that one — swapping the route under an already-shown
    quote would have them signing a number they never read.
    """
    _, _, venue, amount, sell, buy = cb.data.split(":", 5)
    await cb.answer()
    from app.handlers.home import as_person

    await swap_cmd(as_person(cb), _VenueArgs(f"{amount} {sell} {buy} on {venue}"))


class _VenueArgs:
    """A stand-in for aiogram's CommandObject carrying just the arguments."""

    def __init__(self, args: str):
        self.args = args
        self.command = None
        self.prefix = "/"
        self.mention = None
        self.magic_result = None
