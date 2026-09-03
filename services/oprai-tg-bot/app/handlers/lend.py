"""Lending and borrowing on Morpho.

Every market on Robinhood Chain lends USDG against a different collateral, so
the commands are shaped around what someone actually wants:

    /lend 100          supply 100 USDG and earn — we pick the best rate
    /borrow 50         borrow 50 USDG — we work out the collateral needed
    /repay             clear the loan
    /withdraw          take the supply back

Nobody should have to know what a market id is, or compute a safe loan-to-value
by hand. `/borrow 50` names the one number a borrower actually has in mind, and
the collateral is derived from it at a ratio that leaves room to move — a loan
opened at the liquidation threshold is liquidated by the first tick against it.
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
from app.services import evm, morpho
from app.services import tokens as tok
from app.services import wallet as wallet_svc
from app.signer_client import SignerError

router = Router(name="lend")

_pending: dict[str, dict] = {}

# How much of the liquidation threshold to actually use. Borrowing at the
# limit means the first move against you is a liquidation, so the offer is
# sized well inside it and the headroom is stated rather than implied.
SAFE_LTV_FRACTION = 0.75

EXPLORER = "https://robinscan.io/tx/"


def _fmt(x: float, places: int = 2) -> str:
    return f"{x:,.{places}f}".rstrip("0").rstrip(".") if places else f"{x:,.0f}"


def _amount(text: str) -> float | None:
    try:
        value = float(Decimal(text.lstrip("$").replace(",", "")))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None
    return value if value > 0 else None


def _human(base: str | int, decimals: int) -> float:
    return float(Decimal(str(base)) / (10 ** int(decimals)))


async def _context(telegram_id: int) -> tuple[str, str, list[dict]]:
    addr = await wallet_svc.wallet_address(telegram_id)
    jwt = await auth_svc.get_jwt(telegram_id)
    return addr, jwt, await morpho.markets(jwt)


# ── status ──────────────────────────────────────────────────────────────────
@router.message(Command("lend", "borrow", "repay", "withdraw"))
async def lend_router(message: Message, command: CommandObject) -> None:
    """One entry point: the four verbs differ only in what they do with the
    same market and position data, so they share the lookup."""
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    verb = message.text.split()[0].lstrip("/").split("@")[0].lower()
    args = (command.args or "").split()

    try:
        addr, jwt, markets = await _context(user.id)
        if not markets:
            await message.answer("No lending markets are available right now.")
            return
        positions = await morpho.positions(jwt, addr)
    except (morpho.MorphoError, auth_svc.AuthError) as e:
        await message.answer(f"⚠️ Couldn't read the lending markets: {e}")
        return

    if verb == "lend":
        await _lend(message, addr, jwt, markets, positions, args)
    elif verb == "borrow":
        await _borrow(message, addr, jwt, markets, args)
    elif verb == "repay":
        await _close(message, addr, jwt, markets, positions, args, kind="repay")
    else:
        await _close(message, addr, jwt, markets, positions, args, kind="withdraw")


async def _overview(markets: list[dict], positions: list[dict]) -> str:
    lines = ["<b>Lending</b> · Morpho on Robinhood Chain", ""]
    if positions:
        lines.append("<b>Your positions</b>")
        for p in positions:
            supplied = float(p.get("supplyAssets") or 0)
            borrowed = float(p.get("borrowAssets") or 0)
            collateral = float(p.get("collateral") or 0)
            bits = []
            if supplied:
                bits.append(f"supplied {_fmt(supplied)} {p.get('loanSymbol')}")
            if borrowed:
                bits.append(f"borrowed {_fmt(borrowed)} {p.get('loanSymbol')}")
            if collateral:
                bits.append(f"collateral {_fmt(collateral)} {p.get('collateralSymbol')}")
            health = p.get("healthFactor")
            if borrowed and health:
                bits.append(f"health {float(health):.2f}")
            lines.append(f"• {' · '.join(bits)}")
        lines.append("")

    lines.append("<b>Rates</b>")
    for m in sorted(markets, key=lambda x: -float(x.get("supplyApy") or 0)):
        lines.append(
            f"• <b>{m['collateralSymbol']}</b> → borrow {m['loanSymbol']} · "
            f"earn {morpho.apy_pct(m['supplyApy']):.2f}% · "
            f"pay {morpho.apy_pct(m['borrowApy']):.2f}%"
        )
    lines += [
        "",
        "<code>/lend 100</code> — supply USDG and earn",
        "<code>/borrow 50</code> — borrow USDG against collateral",
        "<code>/repay</code> · <code>/withdraw</code>",
    ]
    return "\n".join(lines)


# ── supply ──────────────────────────────────────────────────────────────────
async def _lend(message: Message, addr: str, jwt: str, markets: list[dict],
                positions: list[dict], args: list[str]) -> None:
    if not args:
        await message.answer(await _overview(markets, positions))
        return
    amount = _amount(args[0])
    if amount is None:
        await message.answer("Usage: <code>/lend 100</code> — the amount of USDG to supply.")
        return

    market = morpho.best_supply_market(markets)
    loan = market["loanAddress"]
    held = await tok.token_balance(loan, addr)
    want = int(Decimal(str(amount)) * (10 ** int(market["loanDecimals"])))
    if held < want:
        have = _human(held, market["loanDecimals"])
        await message.answer(
            f"You hold <b>{_fmt(have)} {market['loanSymbol']}</b> and asked to "
            f"supply {_fmt(amount)}.\n\n"
            f"Get some with <code>/swap 0.05 ETH {market['loanSymbol']}</code>."
        )
        return

    pid = secrets.token_urlsafe(8)
    _pending[pid] = {"telegram_id": message.from_user.id, "kind": "supply",
                     "market": market, "amount": amount}
    yearly = amount * float(market["supplyApy"] or 0)
    await audit(message.from_user.id, "lend_quoted",
                {"amount": amount, "market": market["marketId"]})
    await message.answer(
        f"<b>Supply {_fmt(amount)} {market['loanSymbol']}</b>\n\n"
        f"Rate: <b>{morpho.apy_pct(market['supplyApy']):.2f}%</b> — about "
        f"{_fmt(yearly)} {market['loanSymbol']} a year\n"
        f"Market: {market['collateralSymbol']} collateral\n\n"
        "<i>You can withdraw at any time with /withdraw.</i>",
        reply_markup=_confirm_kb(pid, "Supply"),
    )


# ── borrow ──────────────────────────────────────────────────────────────────
async def _borrow(message: Message, addr: str, jwt: str, markets: list[dict],
                  args: list[str]) -> None:
    amount = _amount(args[0]) if args else None
    if amount is None:
        await message.answer(
            "Usage: <code>/borrow 50</code> — how much USDG you want.\n\n"
            "<i>I'll work out the collateral needed and check you have it.</i>"
        )
        return

    # Offer only the markets whose collateral this wallet actually holds. An
    # option someone cannot take is not an option, it is a dead end.
    options = []
    for m in markets:
        needed = _collateral_needed(m, amount)
        held = await tok.token_balance(m["collateralAddress"], addr)
        have = _human(held, m["collateralDecimals"])
        options.append({"market": m, "needed": needed, "have": have})

    affordable = [o for o in options if o["have"] >= o["needed"] > 0]
    if not affordable:
        lines = [
            f"To borrow <b>{_fmt(amount)} USDG</b> you need collateral, and this "
            "wallet doesn't hold enough of any accepted token:",
            "",
        ]
        for o in sorted(options, key=lambda x: -x["have"])[:4]:
            m = o["market"]
            lines.append(
                f"• <b>{m['collateralSymbol']}</b> — need {_fmt(o['needed'], 4)}, "
                f"you have {_fmt(o['have'], 4)}"
            )
        lines += ["", "<i>Buy collateral with /swap, then try again.</i>"]
        await message.answer("\n".join(lines))
        return

    pid = secrets.token_urlsafe(8)
    _pending[pid] = {"telegram_id": message.from_user.id, "kind": "borrow",
                     "amount": amount, "options": affordable}
    rows = [[InlineKeyboardButton(
        text=f"{o['market']['collateralSymbol']} — {_fmt(o['needed'], 4)} "
             f"({morpho.apy_pct(o['market']['borrowApy']):.1f}%)",
        callback_data=f"lnd:c:{i}:{pid}")]
        for i, o in enumerate(affordable[:4])]
    rows.append([InlineKeyboardButton(text="Cancel", callback_data=f"lnd:no:{pid}")])

    await audit(message.from_user.id, "borrow_quoted", {"amount": amount})
    await message.answer(
        f"<b>Borrow {_fmt(amount)} USDG</b>\n\n"
        "Which collateral do you want to post?\n\n"
        f"<i>Sized to about {int(SAFE_LTV_FRACTION * 100)}% of what each market "
        "allows, so a normal move in price doesn't liquidate you.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def _collateral_needed(market: dict, borrow_usd_amount: float) -> float:
    """Collateral for a loan, sized inside the liquidation threshold.

    The market's LLTV is where liquidation begins, not where borrowing should
    aim — we use a fraction of it so the position has somewhere to go.
    """
    lltv = float(market.get("lltvPct") or 0) / 100
    loan_price = float(market.get("loanPriceUsd") or 0)
    collateral_price = float(market.get("collateralPriceUsd") or 0)
    if lltv <= 0 or collateral_price <= 0 or loan_price <= 0:
        return 0.0
    borrow_value = borrow_usd_amount * loan_price
    return borrow_value / (lltv * SAFE_LTV_FRACTION) / collateral_price


@router.callback_query(F.data.startswith("lnd:c:"))
async def borrow_choose(cb: CallbackQuery) -> None:
    _, _, index, pid = cb.data.split(":", 3)
    p = _pending.get(pid)
    if not p or cb.from_user.id != p["telegram_id"]:
        await cb.answer("This isn't yours, or it expired.", show_alert=True)
        return
    option = p["options"][int(index)]
    p["market"], p["collateral"] = option["market"], option["needed"]
    await cb.answer()

    m = option["market"]
    ltv = SAFE_LTV_FRACTION * float(m.get("lltvPct") or 0)
    await cb.message.edit_text(
        f"<b>Borrow {_fmt(p['amount'])} {m['loanSymbol']}</b>\n\n"
        f"Post: <b>{_fmt(option['needed'], 4)} {m['collateralSymbol']}</b>\n"
        f"Rate: {morpho.apy_pct(m['borrowApy']):.2f}% a year\n"
        f"Loan-to-value: about {ltv:.0f}% of a {float(m['lltvPct']):.1f}% limit\n\n"
        "<i>If the collateral falls far enough, the position is liquidated. "
        "Repay any time with /repay.</i>",
        reply_markup=_confirm_kb(pid, "Borrow"),
    )


# ── repay / withdraw ────────────────────────────────────────────────────────
async def _close(message: Message, addr: str, jwt: str, markets: list[dict],
                 positions: list[dict], args: list[str], *, kind: str) -> None:
    field = "borrowAssets" if kind == "repay" else "supplyAssets"
    live = [p for p in positions if float(p.get(field) or 0) > 0]
    if not live:
        await message.answer(
            "You have nothing to repay." if kind == "repay"
            else "You have nothing supplied to withdraw."
        )
        return

    amount = _amount(args[0]) if args and args[0].lower() not in ("all", "max") else None
    position = live[0]
    market = next(
        (m for m in markets if m["marketId"].lower() == position["marketId"].lower()),
        None,
    )
    if market is None:
        await message.answer("That market isn't available right now.")
        return

    outstanding = float(position.get(field) or 0)
    if amount is not None and amount > outstanding:
        await message.answer(
            f"You only have {_fmt(outstanding)} {position['loanSymbol']} "
            f"{'borrowed' if kind == 'repay' else 'supplied'}. "
            f"Send <code>/{kind}</code> on its own to close it out."
        )
        return

    pid = secrets.token_urlsafe(8)
    _pending[pid] = {"telegram_id": message.from_user.id, "kind": kind,
                     "market": market, "amount": amount}
    verb = "Repay" if kind == "repay" else "Withdraw"
    shown = f"{_fmt(amount)} {position['loanSymbol']}" if amount is not None \
        else f"all {_fmt(outstanding)} {position['loanSymbol']}"
    note = (
        "<i>Repaying everything clears the interest accrued right up to the "
        "block, so the loan actually closes.</i>" if amount is None and kind == "repay"
        else ""
    )
    await message.answer(
        f"<b>{verb} {shown}</b>\n"
        f"Market: {market['collateralSymbol']} / {market['loanSymbol']}\n\n{note}",
        reply_markup=_confirm_kb(pid, verb),
    )


# ── confirm ─────────────────────────────────────────────────────────────────
def _confirm_kb(pid: str, verb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ {verb}", callback_data=f"lnd:ok:{pid}"),
        InlineKeyboardButton(text="Cancel", callback_data=f"lnd:no:{pid}"),
    ]])


@router.callback_query(F.data.startswith("lnd:"))
async def lend_confirm(cb: CallbackQuery) -> None:
    _, action, pid = cb.data.split(":", 2)
    p = _pending.get(pid)
    if not p:
        await cb.answer("This expired. Run the command again.", show_alert=True)
        return
    if cb.from_user.id != p["telegram_id"]:
        await cb.answer("This isn't yours.", show_alert=True)
        return
    if action == "no":
        _pending.pop(pid, None)
        await cb.answer("Cancelled")
        await cb.message.edit_text("Cancelled — nothing was signed.")
        return
    if action != "ok" or "market" not in p:
        await cb.answer("Pick an option first.", show_alert=True)
        return

    _pending.pop(pid, None)
    await cb.answer()
    await cb.message.edit_text("Preparing…")

    async def progress(i: int, total: int) -> None:
        if total > 1:
            await cb.message.edit_text(f"Signing step {i} of {total}…")

    try:
        addr = await wallet_svc.wallet_address(p["telegram_id"])
        jwt = await auth_svc.get_jwt(p["telegram_id"])
        market, kind = p["market"], p["kind"]

        if kind == "supply":
            built = await morpho.build_supply(jwt, addr, market, p["amount"])
        elif kind == "borrow":
            built = await morpho.build_borrow(
                jwt, addr, market, borrow=p["amount"], collateral=p["collateral"]
            )
        elif kind == "repay":
            built = await morpho.build_repay(jwt, addr, market, p["amount"])
        else:
            built = await morpho.build_withdraw(jwt, addr, market, p["amount"])

        w = await wallet_svc.get_or_create_wallet(p["telegram_id"])
        hashes = await morpho.execute(w["enc_key_ref"], addr, built, on_step=progress)
    except (morpho.MorphoError, auth_svc.AuthError, evm.EvmError, SignerError) as e:
        log.warning("lend_failed", telegram_id=p["telegram_id"], kind=p["kind"],
                    error=str(e))
        await audit(p["telegram_id"], "lend_failed",
                    {"kind": p["kind"], "error": str(e)[:200]})
        await cb.message.edit_text(f"❌ Couldn't complete that: {e}")
        return

    await audit(p["telegram_id"], "lend_done",
                {"kind": p["kind"], "hash": hashes[-1]})
    done = {"supply": "Supplied", "borrow": "Borrowed",
            "repay": "Repaid", "withdraw": "Withdrew"}[p["kind"]]
    link = f'<a href="{EXPLORER}{hashes[-1]}">{hashes[-1][:10]}…</a>'
    await cb.message.edit_text(
        f"✅ <b>{done}.</b>\n{link}\n\nSee where you stand with <code>/lend</code>."
    )
