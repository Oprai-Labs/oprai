"""/alpha — real-time alpha: track wallets, get smart-money buy alerts.

Based-bot-style, button-driven. `/alpha` opens a menu; users add wallets to track
and toggle the smart-money discovery feed. The background worker (alert_worker)
pings them; every alert carries Buy / Analyze buttons so a signal is one tap from
action. State lives in tg_schema via AlertStore; keys/trades go through the existing
signer+gateway flow."""
from __future__ import annotations

import re

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from app.db import audit, upsert_tg_user
from app.handlers.privacy import private_answer
from app.logging_config import log
from app.services.alert_store import AlertStore
from app.services.signals_client import SignalsClient, SignalsError

router = Router(name="alpha")
_store = AlertStore()
_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


# ── keyboard helpers ───────────────────────────────────────────────────────

def kb_from_specs(specs: list[tuple[str, str, str]]) -> InlineKeyboardMarkup:
    """alerts.py (label, kind, payload) specs → an inline keyboard (one row)."""
    row = [InlineKeyboardButton(text=label, callback_data=f"{kind}:{payload}")
           for label, kind, payload in specs]
    return InlineKeyboardMarkup(inline_keyboard=[row])


def make_alert_sender(bot: Bot):
    """The send seam the worker calls: (chat_id, html_text, button_specs) → deliver."""
    async def send(chat_id: int, text: str, button_specs: list) -> None:
        try:
            await bot.send_message(chat_id, text,
                                   reply_markup=kb_from_specs(button_specs))
        except Exception as e:  # a blocked/deleted chat must not kill the worker
            log.warning("alert_send_failed", chat_id=chat_id, error=str(e)[:120])
    return send


async def _tip() -> int:
    try:
        return await SignalsClient().tip()
    except SignalsError:
        return 0


def _menu_kb(sub: dict | None) -> InlineKeyboardMarkup:
    on = bool(sub and sub.get("smart_alerts"))
    toggle = ("🔕 Smart alerts: ON — tap to stop" if on
              else "🔔 Smart alerts: OFF — tap to start")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle, callback_data="toggle_smart:x")],
        [InlineKeyboardButton(text="👛 My tracked wallets", callback_data="my_wallets:x")],
    ])


MENU = (
    "⚡️ <b>OPRAI Alpha</b> — real-time on Robinhood Chain\n\n"
    "• <b>Track wallets</b> — <code>/track 0x…</code> and I ping you the instant "
    "they buy (smart wallets flagged 🧠).\n"
    "• <b>Smart-money feed</b> — turn it on to get alerted when smart money piles "
    "into a token (fresh launches flagged 🆕).\n\n"
    "Every alert has <b>Buy</b> and <b>Analyze</b> one tap away."
)


# ── commands ────────────────────────────────────────────────────────────────

@router.message(Command("alpha"))
async def alpha_menu(message: Message) -> None:
    u = message.from_user
    await upsert_tg_user(u.id, u.username)
    sub = await _store.get_sub(u.id)
    await private_answer(message, MENU, reply_markup=_menu_kb(sub))


@router.message(Command("track"))
async def track(message: Message, command: CommandObject) -> None:
    u = message.from_user
    await upsert_tg_user(u.id, u.username)
    arg = (command.args or "").strip().split()
    addr = arg[0] if arg else ""
    if not _EVM_RE.match(addr):
        await message.answer("Usage: <code>/track 0x…</code> (a Robinhood-Chain wallet)")
        return
    label = " ".join(arg[1:])[:40] or None
    tip = await _tip()
    added = await _store.add_tracked_wallet(u.id, addr, tip, label)
    await audit(u.id, "alpha_track", {"address": addr, "added": added})
    if added:
        await message.answer(
            f"✅ Tracking <code>{addr}</code>. I'll ping you on their next buy.")
    else:
        await message.answer("You're already tracking that wallet.")


@router.message(Command("untrack"))
async def untrack(message: Message, command: CommandObject) -> None:
    u = message.from_user
    addr = (command.args or "").strip().split()[0] if command.args else ""
    if not _EVM_RE.match(addr):
        await message.answer("Usage: <code>/untrack 0x…</code>")
        return
    removed = await _store.remove_tracked_wallet(u.id, addr)
    await message.answer("✅ Untracked." if removed else "You weren't tracking that.")


# ── callbacks ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("toggle_smart:"))
async def cb_toggle_smart(cq: CallbackQuery) -> None:
    uid = cq.from_user.id
    sub = await _store.get_sub(uid)
    new_on = not (sub and sub.get("smart_alerts"))
    tip = await _tip()
    await _store.set_smart_alerts(uid, new_on, tip)
    await audit(uid, "alpha_smart_toggle", {"on": new_on})
    await cq.message.edit_reply_markup(reply_markup=_menu_kb({"smart_alerts": new_on}))
    await cq.answer("Smart alerts ON — watching smart money 🧠" if new_on
                    else "Smart alerts off.")


@router.callback_query(F.data.startswith("my_wallets:"))
async def cb_my_wallets(cq: CallbackQuery) -> None:
    rows = await _store.list_tracked(cq.from_user.id)
    if not rows:
        await private_answer(cq.message, "No tracked wallets yet. Add one: <code>/track 0x…</code>",
                             to_user_id=cq.from_user.id)
    else:
        lines = ["<b>Tracked wallets</b>"]
        for r in rows:
            lbl = f" — {r['label']}" if r.get("label") else ""
            lines.append(f"• <code>{r['address']}</code>{lbl}")
        lines.append("\nRemove one with <code>/untrack 0x…</code>")
        await private_answer(cq.message, "\n".join(lines), to_user_id=cq.from_user.id)
    await cq.answer()


@router.callback_query(F.data.startswith("analyze:"))
async def cb_analyze(cq: CallbackQuery) -> None:
    token = cq.data.split(":", 1)[1]
    await cq.answer("Analyzing…")
    try:
        rep = await SignalsClient().token_report(token)
    except SignalsError as e:
        await cq.message.answer(f"⚠️ Couldn't analyze right now: {str(e)[:100]}")
        return
    f = rep.get("facts", {}) if isinstance(rep, dict) else {}
    if rep.get("status") != "ok":
        await cq.message.answer("No Robinhood-Chain data for that token.")
        return
    risk = f.get("risk_label") or "?"
    lines = [
        f"🔍 <b>Token X-ray</b> · risk <b>{risk}</b> ({f.get('risk_score', '?')}/100)",
        f"• Holders: <b>{f.get('holders', '?')}</b>",
        f"• Top-10 wallets: <b>{f.get('top10_pct', '?')}%</b> · LP {f.get('lp_pct', 0)}% · burned {f.get('burned_pct', 0)}%",
        f"• Smart money: <b>{f.get('smart_money_holders', 0)}</b> wallets, {f.get('smart_money_holding_pct', 0)}% of supply",
    ]
    if f.get("launchpad"):
        lines.append(f"• Launched via <b>{f['launchpad']}</b>")
    lines.append(f"<code>{token}</code>")
    await cq.message.answer("\n".join(lines))


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(cq: CallbackQuery) -> None:
    """Buy the token an alert is about.

    This used to say one-tap buying was "coming next" and send people to
    /wallet, which does not trade. The trade flow exists now, so the button
    quotes a real swap: the point of an alert is to be able to act on it while
    it is still worth acting on.
    """
    token = cq.data.split(":", 1)[1]
    await cq.answer()
    await cq.message.answer(
        f"To buy it, name your size:\n\n"
        f"<code>/swap 0.01 ETH {token}</code>\n\n"
        "<i>You'll get a quote to confirm before anything is signed.</i>"
    )
