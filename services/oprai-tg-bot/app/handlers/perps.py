"""Perpetuals on Lighter — leveraged stocks and memecoins, from chat.

Orders are signed server-side with a delegated agent key, so trading costs no
gas and needs no wallet interaction. The one-time authorisation is done
silently on the first trade rather than made into a step the user must know
about.

Funding is the one on-chain part: USDG has to reach the Lighter account, and
that deposit is what creates the account in the first place.
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
from app.services import evm, lighter
from app.services import tokens as tok
from app.services import wallet as wallet_svc
from app.signer_client import SignerError

router = Router(name="perps")

_pending: dict[str, dict] = {}

TRADE_USAGE = (
    "Usage: <code>/long &lt;SYMBOL&gt; &lt;collateral $&gt; [leverage]</code>\n"
    "Examples:\n"
    "• <code>/long NVDA 50 5</code> — $50 at 5x\n"
    "• <code>/short TSLA 25 3</code>"
)


def _fmt(x: float, places: int = 4) -> str:
    return f"{x:.{places}f}".rstrip("0").rstrip(".") or "0"


def _parse_trade(args: list[str]) -> tuple[str, float, int] | None:
    if len(args) < 2:
        return None
    symbol = args[0].upper().lstrip("$")
    try:
        collateral = float(Decimal(args[1].lstrip("$")))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None
    leverage = 1
    if len(args) > 2:
        try:
            leverage = int(float(args[2].lower().rstrip("x")))
        except (ValueError, ArithmeticError):
            return None
    if collateral <= 0 or leverage < 1:
        return None
    return symbol, collateral, leverage


# ── status ──────────────────────────────────────────────────────────────────
@router.message(Command("perps", "positions"))
async def perps_cmd(message: Message, command: CommandObject) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    args = (command.args or "").split()

    if args and args[0].lower() == "deposit":
        await _deposit(message, args[1:])
        return

    addr = await wallet_svc.wallet_address(user.id)
    try:
        jwt = await auth_svc.get_jwt(user.id)
        state = await lighter.account(jwt, addr)
    except (lighter.LighterError, auth_svc.AuthError) as e:
        await private_answer(message, f"⚠️ Couldn't read your perps account: {e}")
        return

    await audit(user.id, "perps_status", {})
    if not state.get("has_account"):
        await private_answer(message, 
            "<b>Perps</b> · Lighter\n\nYou don't have a perps account yet — a USDG "
            "deposit creates one.\n\n"
            "<code>/perps deposit 50</code>\n\n"
            "<i>Need USDG? <code>/swap 0.02 ETH USDG</code> first. "
            "Trading itself is gas-free.</i>"
        )
        return

    lines = [
        "<b>Perps</b> · Lighter",
        "",
        f"Collateral: <b>${_fmt(float(state.get('collateral') or 0), 2)}</b>",
        f"Available: ${_fmt(float(state.get('available_balance') or 0), 2)}",
    ]
    positions = state.get("positions") or []
    if positions:
        lines.append("")
        for p in positions:
            pnl = float(p.get("unrealized_pnl") or 0)
            sign = "+" if pnl >= 0 else ""
            lines.append(
                f"{'🟢' if p.get('side') == 'long' else '🔴'} <b>{p.get('symbol')}</b> "
                f"{p.get('side')} {_fmt(float(p.get('size') or 0))} "
                f"@ {_fmt(float(p.get('entry_price') or 0), 2)}\n"
                f"    PnL {sign}${_fmt(pnl, 2)} · {_fmt(float(p.get('leverage') or 0), 1)}x "
                f"· liq {_fmt(float(p.get('liquidation_price') or 0), 2)}"
            )
        lines += ["", "Close one with <code>/close SYMBOL</code>"]
    else:
        lines += ["", "No open positions.", TRADE_USAGE]

    if not state.get("onboarded"):
        lines += ["", "<i>Your first trade will authorise gas-free order signing.</i>"]
    await message.answer("\n".join(lines))


async def _deposit(message: Message, args: list[str]) -> None:
    user = message.from_user
    if not args:
        await message.answer("Usage: <code>/perps deposit &lt;amount USDG&gt;</code>")
        return
    try:
        amount = float(Decimal(args[0].lstrip("$")))
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError, ArithmeticError):
        await message.answer("That amount doesn't look right.")
        return

    addr = await wallet_svc.wallet_address(user.id)
    try:
        jwt = await auth_svc.get_jwt(user.id)
        usdg = (await tok.resolve("USDG"))[0]
        held = await tok.token_balance(usdg["address"], addr)
    except Exception as e:  # noqa: BLE001
        await private_answer(message, f"⚠️ Couldn't check your USDG: {e}")
        return

    want = int(amount * 10 ** usdg["decimals"])
    if held < want:
        have = Decimal(held) / (10 ** usdg["decimals"])
        await private_answer(message, 
            f"You hold <b>{_fmt(float(have), 2)} USDG</b> and tried to deposit "
            f"{_fmt(amount, 2)}.\n\nGet some with <code>/swap 0.02 ETH USDG</code>."
        )
        return

    await private_answer(message, f"Depositing {_fmt(amount, 2)} USDG to Lighter…")
    try:
        w = await wallet_svc.get_or_create_wallet(user.id)
        tx_hash = await lighter.deposit(jwt, w["enc_key_ref"], addr, amount)
    except (lighter.LighterError, evm.EvmError, SignerError) as e:
        await audit(user.id, "perps_deposit_failed", {"error": str(e)[:200]})
        await private_answer(message, f"❌ Deposit failed: {e}")
        return

    await audit(user.id, "perps_deposit", {"amount": amount, "hash": tx_hash})

    # Record it BEFORE waiting. Lighter's bridge takes as long as it takes, and
    # a wait held only in this task dies with a restart or with the timeout —
    # which left people watching "waiting…" for ever while their money sat
    # credited on the other side. The row is what lets us come back and say so.
    await lighter.remember_deposit(user.id, message.chat.id, addr, tx_hash, amount)

    await private_answer(message,
        f"⏳ Sent {_fmt(amount, 2)} USDG — waiting for Lighter to credit it…"
    )
    state = await lighter.wait_for_account(jwt, addr)
    if state.get("has_account"):
        await lighter.mark_credited(tx_hash)
        await private_answer(message,
            f"✅ Perps account funded — ${_fmt(float(state.get('collateral') or 0), 2)} "
            f"collateral.\n\n{TRADE_USAGE}"
        )
    else:
        await private_answer(message,
            "⏳ Lighter hasn't credited it yet — their bridge sweeps on its own "
            "schedule. I'll message you the moment it lands; you don't need to "
            "keep checking."
        )


# ── open ────────────────────────────────────────────────────────────────────
@router.message(Command("long", "short"))
async def open_cmd(message: Message, command: CommandObject) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    side = "long" if message.text.split()[0].lstrip("/").split("@")[0] == "long" else "short"

    parsed = _parse_trade((command.args or "").split())
    if not parsed:
        await message.answer(TRADE_USAGE)
        return
    symbol, collateral, leverage = parsed

    addr = await wallet_svc.wallet_address(user.id)
    try:
        jwt = await auth_svc.get_jwt(user.id)
        market = await lighter.market_for(jwt, symbol)
        if market is None:
            await message.answer(
                f"<b>{symbol}</b> isn't a Lighter market. Try a stock (NVDA, TSLA) "
                "or a listed token."
            )
            return
        state = await lighter.account(jwt, addr)
    except (lighter.LighterError, auth_svc.AuthError) as e:
        await message.answer(f"⚠️ Couldn't price that: {e}")
        return

    max_lev = int(market.get("max_leverage") or 1)
    if leverage > max_lev:
        await private_answer(message, 
            f"{symbol} allows up to <b>{max_lev}x</b> — you asked for {leverage}x."
        )
        return

    # Guide to a size that will actually fill rather than letting the exchange
    # reject it: the floor is the larger of Lighter's two minimums.
    floor = lighter.min_collateral_usd(market, leverage)
    if collateral < floor:
        await private_answer(message, 
            f"${_fmt(collateral, 2)} is below {symbol}'s minimum at {leverage}x.\n"
            f"You need at least <b>${_fmt(floor, 2)}</b> collateral "
            f"(or more leverage)."
        )
        return

    if not state.get("has_account"):
        await private_answer(message, 
            "You need a perps account first — a USDG deposit creates one:\n"
            "<code>/perps deposit 50</code>"
        )
        return
    available = float(state.get("available_balance") or state.get("collateral") or 0)
    if available < collateral:
        await private_answer(message, 
            f"Your perps account has <b>${_fmt(available, 2)}</b> available and "
            f"you asked to use ${_fmt(collateral, 2)}.\n\n"
            f"Top up with <code>/perps deposit {int(collateral)}</code>."
        )
        return

    mark = float(market.get("mark_price") or market.get("last_price") or 0)
    notional = collateral * leverage
    pid = secrets.token_urlsafe(8)
    _pending[pid] = {
        "telegram_id": user.id, "symbol": symbol, "side": side,
        "collateral": collateral, "leverage": leverage,
    }
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Confirm {side}", callback_data=f"perp:ok:{pid}"),
        InlineKeyboardButton(text="Cancel", callback_data=f"perp:no:{pid}"),
    ]])
    await audit(user.id, "perp_quoted",
                {"symbol": symbol, "side": side, "collateral": collateral, "lev": leverage})
    detail = (
        f"{'🟢' if side == 'long' else '🔴'} <b>{side.title()} {symbol}</b> · Lighter\n\n"
        f"Collateral: ${_fmt(collateral, 2)}\n"
        f"Leverage: {leverage}x → position ${_fmt(notional, 2)}\n"
    )
    if mark:
        detail += f"Mark: ${_fmt(mark, 2)}\n"
        detail += f"Size: ~{_fmt(notional / mark)} {symbol}\n"
    detail += (
        "\n<i>Market order, gas-free. Leverage magnifies losses as well as gains.</i>"
    )
    await private_answer(message, detail, reply_markup=kb)


@router.callback_query(F.data.startswith("perp:"))
async def open_confirm(cb: CallbackQuery) -> None:
    _, action, pid = cb.data.split(":", 2)
    p = _pending.get(pid)
    if not p:
        await cb.answer("This quote expired. Try again.", show_alert=True)
        return
    if cb.from_user.id != p["telegram_id"]:
        await cb.answer("This isn't your trade.", show_alert=True)
        return
    if action == "no":
        _pending.pop(pid, None)
        await cb.answer("Cancelled")
        await cb.message.edit_text("Cancelled — no position opened.")
        return

    _pending.pop(pid, None)
    await cb.answer()
    await cb.message.edit_text(
        f"Opening {p['side']} {p['symbol']} at {p['leverage']}x…"
    )

    try:
        w = await wallet_svc.get_or_create_wallet(p["telegram_id"])
        jwt = await auth_svc.get_jwt(p["telegram_id"])
        res = await lighter.open_position(
            jwt, w["enc_key_ref"], w["address"],
            symbol=p["symbol"], side=p["side"],
            collateral_usd=p["collateral"], leverage=p["leverage"],
        )
    except (lighter.LighterError, auth_svc.AuthError, SignerError) as e:
        log.warning("perp_open_failed", telegram_id=p["telegram_id"], error=str(e))
        await audit(p["telegram_id"], "perp_open_failed", {"error": str(e)[:200]})
        await cb.message.edit_text(f"❌ Couldn't open that position: {e}")
        return

    await audit(p["telegram_id"], "perp_opened",
                {"symbol": p["symbol"], "side": p["side"], "lev": p["leverage"]})
    size = res.get("base_amount")
    await cb.message.edit_text(
        f"✅ {'🟢' if p['side'] == 'long' else '🔴'} <b>{p['side'].title()} "
        f"{p['symbol']}</b> open\n\n"
        + (f"Size: {_fmt(float(size))} {p['symbol']}\n" if size else "")
        + f"Collateral ${_fmt(p['collateral'], 2)} at {p['leverage']}x\n\n"
        "Track it with /perps · close with "
        f"<code>/close {p['symbol']}</code>"
    )


# ── close ───────────────────────────────────────────────────────────────────
@router.message(Command("close"))
async def close_cmd(message: Message, command: CommandObject) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    args = (command.args or "").split()
    if not args:
        await message.answer("Usage: <code>/close &lt;SYMBOL&gt;</code>")
        return
    symbol = args[0].upper().lstrip("$")

    addr = await wallet_svc.wallet_address(user.id)
    try:
        jwt = await auth_svc.get_jwt(user.id)
        state = await lighter.account(jwt, addr)
    except (lighter.LighterError, auth_svc.AuthError) as e:
        await private_answer(message, f"⚠️ Couldn't read your positions: {e}")
        return

    pos = lighter.position_for(state, symbol)
    if not pos:
        await private_answer(message, f"You have no open {symbol} position.")
        return

    size = float(pos.get("size") or 0)
    pnl = float(pos.get("unrealized_pnl") or 0)
    await private_answer(message, f"Closing {_fmt(size)} {symbol} ({pos.get('side')})…")

    try:
        # Lighter has no "close all" — the size must be named, so it comes from
        # the live position.
        res = await lighter.close_position(
            jwt, addr, symbol=symbol, side=str(pos.get("side")), base_amount=size
        )
    except lighter.LighterError as e:
        log.warning("perp_close_failed", telegram_id=user.id, error=str(e))
        await audit(user.id, "perp_close_failed", {"error": str(e)[:200]})
        await private_answer(message, f"❌ Couldn't close it: {e}")
        return

    await audit(user.id, "perp_closed", {"symbol": symbol, "size": size})
    sign = "+" if pnl >= 0 else ""
    await private_answer(message, 
        f"✅ Closed <b>{_fmt(float(res.get('base_amount') or size))} {symbol}</b>\n"
        f"Unrealised PnL at close: {sign}${_fmt(pnl, 2)}\n\n"
        "See what's left with /perps"
    )
