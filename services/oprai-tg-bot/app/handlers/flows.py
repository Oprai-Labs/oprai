"""The button flows: swap without typing, and wallets you can switch between.

Each screen edits the message it was opened from, so a flow happens in one
place rather than scrolling away as a chat log. State is a small per-person
draft — a swap half-chosen is not worth a database row, and losing it to a
restart costs one extra tap.
"""

from __future__ import annotations

import time
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.db import audit
from app.handlers import menu
from app.handlers.home import as_person
from app.logging_config import log
from app.services import portfolio as pf
from app.services import wallet as wallet_svc

router = Router(name="flows")

# telegram_id -> {from, to, expires}
_draft: dict[int, dict] = {}
DRAFT_TTL_SECONDS = 900

# telegram_id -> what a plain next message means ("swap_amount" | "swap_from" | …)
_expecting: dict[int, dict] = {}
EXPECT_TTL_SECONDS = 300


def _draft_for(telegram_id: int) -> dict:
    d = _draft.get(telegram_id)
    if d is None or d["expires"] < time.monotonic():
        d = {"from": None, "to": None, "expires": time.monotonic() + DRAFT_TTL_SECONDS}
        _draft[telegram_id] = d
    return d


def expects(telegram_id: int) -> dict | None:
    """What the bot is waiting for from this person, if anything."""
    e = _expecting.get(telegram_id)
    if e is None or e["expires"] < time.monotonic():
        _expecting.pop(telegram_id, None)
        return None
    return e


def expect(telegram_id: int, kind: str, **extra) -> None:
    _expecting[telegram_id] = {
        "kind": kind, "expires": time.monotonic() + EXPECT_TTL_SECONDS, **extra
    }


def clear_expect(telegram_id: int) -> None:
    _expecting.pop(telegram_id, None)


async def _holdings(telegram_id: int) -> tuple[list[dict], Decimal]:
    address = await wallet_svc.wallet_address(telegram_id)
    try:
        tokens = await pf.token_holdings(address, telegram_id)
    except Exception:  # noqa: BLE001 — an empty list is a usable answer
        tokens = []
    try:
        native = Decimal(str((await pf.native_balance(telegram_id))["eth"]))
    except Exception:  # noqa: BLE001
        native = Decimal(0)
    return tokens, native


# ── swap ────────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "menu:swap")
async def swap_start(cb: CallbackQuery) -> None:
    await cb.answer()
    tokens, native = await _holdings(cb.from_user.id)
    _draft.pop(cb.from_user.id, None)
    await _edit(cb, "⇄ <b>Swap</b>\n\nWhat are you selling?",
                menu.sell_menu(tokens, native))


@router.callback_query(F.data.startswith("swap:from:"))
async def swap_from(cb: CallbackQuery) -> None:
    symbol = cb.data.split(":", 2)[2]
    await cb.answer()
    if symbol == "?":
        expect(cb.from_user.id, "swap_from")
        await _edit(cb, "⇄ <b>Swap</b>\n\nSend me the ticker or address you want "
                        "to sell — <i>NVDA</i>, <i>USDG</i>, or a 0x… address.")
        return
    _draft_for(cb.from_user.id)["from"] = symbol
    tokens, _ = await _holdings(cb.from_user.id)
    await _edit(cb, f"⇄ <b>Swap {symbol} →</b>\n\nWhat do you want?",
                menu.buy_menu(symbol, tokens))


@router.callback_query(F.data.startswith("swap:to:"))
async def swap_to(cb: CallbackQuery) -> None:
    symbol = cb.data.split(":", 2)[2]
    await cb.answer()
    draft = _draft_for(cb.from_user.id)
    if symbol == "?":
        expect(cb.from_user.id, "swap_to")
        await _edit(cb, "⇄ <b>Swap</b>\n\nSend me the ticker or address you want "
                        "to buy.")
        return
    draft["to"] = symbol
    await _edit(cb, f"⇄ <b>{draft['from']} → {symbol}</b>\n\nHow much "
                    f"{draft['from']}?", menu.amount_menu(draft["from"]))


@router.callback_query(F.data.startswith("swap:amt:"))
async def swap_amount(cb: CallbackQuery) -> None:
    choice = cb.data.split(":", 2)[2]
    await cb.answer()
    draft = _draft_for(cb.from_user.id)
    if not draft.get("from") or not draft.get("to"):
        await _edit(cb, "That swap expired — start again.", menu.home_keyboard())
        return

    if choice == "?":
        expect(cb.from_user.id, "swap_amount",
               **{"from": draft["from"], "to": draft["to"]})
        await _edit(cb, f"⇄ <b>{draft['from']} → {draft['to']}</b>\n\n"
                        f"How much {draft['from']}? Send the number.")
        return

    amount = await _share_of_balance(cb.from_user.id, draft["from"], int(choice))
    if amount is None or amount <= 0:
        await _edit(cb, f"You don't have enough {draft['from']} to swap.",
                    menu.home_keyboard())
        return

    await audit(cb.from_user.id, "swap_via_buttons",
                {"from": draft["from"], "to": draft["to"], "pct": choice})
    await _run_swap(cb, amount, draft["from"], draft["to"])


async def _share_of_balance(telegram_id: int, symbol: str, percent: int) -> Decimal | None:
    tokens, native = await _holdings(telegram_id)
    if symbol.upper() == "ETH":
        return menu.percent_of(native, percent, leave_gas=True)
    for h in tokens:
        if h["symbol"].upper() == symbol.upper():
            held = Decimal(h["amount"]) / (10 ** int(h["decimals"]))
            return menu.percent_of(held, percent, leave_gas=False)
    return None


async def _run_swap(cb: CallbackQuery, amount: Decimal, sell: str, buy: str) -> None:
    """Hand off to the real /swap flow, which owns quoting and confirmation."""
    from app.handlers.swap import swap_cmd

    text = f"{amount:.8f}".rstrip("0").rstrip(".")
    message = as_person(cb)
    await _edit(cb, f"⇄ Getting a quote for <b>{text} {sell} → {buy}</b>…")
    await swap_cmd(message, _ArgString(f"{text} {sell} {buy}"))


class _ArgString:
    """A stand-in for aiogram's CommandObject carrying only the arguments."""

    def __init__(self, args: str):
        self.args = args
        self.command = None
        self.prefix = "/"
        self.mention = None
        self.magic_result = None


# ── wallets ─────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "menu:wallets")
async def wallets_menu(cb: CallbackQuery) -> None:
    await cb.answer()
    await _show_wallets(cb)


async def _show_wallets(cb: CallbackQuery) -> None:
    rows = await wallet_svc.list_wallets(cb.from_user.id)
    if not rows:
        await _edit(cb, "You don't have a wallet yet.", menu.home_keyboard())
        return

    lines = ["🔑 <b>Your wallets</b>", ""]
    pairs: list[tuple[str, str]] = []
    for i, r in enumerate(rows, start=1):
        active = r["archived_at"] is None
        lines.append(
            f"{'✅' if active else '　'} <b>W{i}</b> <code>{r['address']}</code>"
            f"{'' if active else ' · archived'}"
        )
        label = f"{'✅ ' if active else ''}W{i}"
        pairs.append((label, f"wal:pick:{r['address']}"))

    lines += ["", "<i>Tap one to use it. Every wallet stays yours — archived "
              "ones keep their key and can be brought back.</i>"]
    keyboard = menu.grid(pairs, 5)
    keyboard.inline_keyboard.append([
        menu.button("📤 Export key", "wal:export"),
        menu.button("📥 Import", "wal:import"),
    ])
    keyboard.inline_keyboard.append([
        menu.button("🆕 New wallet", "wal:new"),
        menu.button(menu.ICONS["back"], "home:refresh"),
    ])
    await _edit(cb, "\n".join(lines), keyboard)


@router.callback_query(F.data.startswith("wal:pick:"))
async def wallet_pick(cb: CallbackQuery) -> None:
    address = cb.data.split(":", 2)[2]
    row = await wallet_svc.activate(cb.from_user.id, address)
    if row is None:
        await cb.answer("That wallet isn't yours.", show_alert=True)
        return
    await cb.answer("Switched")
    await audit(cb.from_user.id, "wallet_switch", {"address": address})
    await _show_wallets(cb)


async def _edit(cb: CallbackQuery, text: str, keyboard=None) -> None:
    """Replace the screen in place. An unchanged message is not an error."""
    try:
        await cb.message.edit_text(text, reply_markup=keyboard)
    except Exception as e:  # noqa: BLE001
        log.debug("menu_edit_skipped", error=str(e)[:120])


# ── typed answers ───────────────────────────────────────────────────────────
@router.message(F.text & ~F.text.startswith("/"))
async def catch_expected_reply(message: Message) -> None:
    """The one piece a button couldn't offer: an amount, or a ticker we don't
    hold yet.

    Registered ahead of chat's catch-all, so it must hand back anything it
    wasn't waiting for — otherwise every question to OPRAI would be read as a
    swap amount.
    """
    from aiogram.dispatcher.event.bases import SkipHandler

    waiting = expects(message.from_user.id)
    if waiting is None:
        raise SkipHandler

    text = (message.text or "").strip()
    kind = waiting["kind"]

    if kind in ("swap_from", "swap_to"):
        clear_expect(message.from_user.id)
        draft = _draft_for(message.from_user.id)
        draft["from" if kind == "swap_from" else "to"] = text.upper().lstrip("$")
        if draft["from"] and draft["to"]:
            await message.answer(
                f"⇄ <b>{draft['from']} → {draft['to']}</b>\n\nHow much "
                f"{draft['from']}?",
                reply_markup=menu.amount_menu(draft["from"]),
            )
        else:
            tokens, _ = await _holdings(message.from_user.id)
            await message.answer(
                f"⇄ <b>Swap {draft['from']} →</b>\n\nWhat do you want?",
                reply_markup=menu.buy_menu(draft["from"] or "", tokens),
            )
        return

    if kind == "swap_amount":
        try:
            amount = Decimal(text.replace(",", "."))
            if amount <= 0:
                raise ValueError
        except Exception:  # noqa: BLE001 — anything unparseable is just not a number
            await message.answer("That doesn't look like an amount — try again.")
            return
        clear_expect(message.from_user.id)
        from app.handlers.swap import swap_cmd

        amount_text = f"{amount:f}".rstrip("0").rstrip(".")
        await swap_cmd(
            message, _ArgString(f"{amount_text} {waiting['from']} {waiting['to']}")
        )
        return

    raise SkipHandler
