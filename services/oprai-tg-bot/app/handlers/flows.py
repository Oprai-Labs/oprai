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


# ── bridge ──────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "menu:bridge")
async def bridge_start(cb: CallbackQuery) -> None:
    await cb.answer()
    await _edit(cb, "🌉 <b>Bring funds in</b>\n\nWhich chain are they on now?",
                menu.bridge_menu())


@router.callback_query(F.data.startswith("br:from:"))
async def bridge_from(cb: CallbackQuery) -> None:
    chain = cb.data.split(":", 2)[2]
    await cb.answer()
    _draft_for(cb.from_user.id)["chain"] = chain
    await _edit(cb, f"🌉 <b>From {chain.title()}</b>\n\nHow much ETH?",
                menu.bridge_amount_menu(chain))


@router.callback_query(F.data.startswith("br:amt:"))
async def bridge_amount(cb: CallbackQuery) -> None:
    choice = cb.data.split(":", 2)[2]
    await cb.answer()
    chain = _draft_for(cb.from_user.id).get("chain")
    if not chain:
        await _edit(cb, "That expired — start again.", menu.home_keyboard())
        return
    if choice == "?":
        expect(cb.from_user.id, "bridge_amount", chain=chain)
        await _edit(cb, f"🌉 <b>From {chain.title()}</b>\n\nHow much ETH? "
                        "Send the number.")
        return
    await _run_bridge(cb, choice, chain)


async def _run_bridge(cb: CallbackQuery, amount: str, chain: str) -> None:
    from app.handlers.bridge import bridge_cmd

    await _edit(cb, f"🌉 Quoting <b>{amount} ETH</b> from {chain.title()}…")
    await bridge_cmd(as_person(cb), _ArgString(f"{amount} ETH from {chain}"))


# ── send ────────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "menu:send")
async def send_start(cb: CallbackQuery) -> None:
    await cb.answer()
    tokens, native = await _holdings(cb.from_user.id)
    _draft.pop(cb.from_user.id, None)
    await _edit(cb, "📤 <b>Send</b>\n\nWhat are you sending?",
                menu.send_token_menu(tokens, native))


@router.callback_query(F.data.startswith("snd:tok:"))
async def send_token(cb: CallbackQuery) -> None:
    symbol = cb.data.split(":", 2)[2]
    await cb.answer()
    _draft_for(cb.from_user.id)["send_token"] = symbol
    await _edit(cb, f"📤 <b>Send {symbol}</b>\n\nHow much?",
                menu.send_amount_menu())


@router.callback_query(F.data.startswith("snd:amt:"))
async def send_amount(cb: CallbackQuery) -> None:
    choice = cb.data.split(":", 2)[2]
    await cb.answer()
    draft = _draft_for(cb.from_user.id)
    symbol = draft.get("send_token")
    if not symbol:
        await _edit(cb, "That expired — start again.", menu.home_keyboard())
        return

    if choice == "?":
        expect(cb.from_user.id, "send_amount", symbol=symbol)
        await _edit(cb, f"📤 <b>Send {symbol}</b>\n\nHow much? Send the number.")
        return

    amount = await _share_of_balance(cb.from_user.id, symbol, int(choice))
    if amount is None or amount <= 0:
        await _edit(cb, f"You don't have enough {symbol} to send.",
                    menu.home_keyboard())
        return

    text = f"{amount:.8f}".rstrip("0").rstrip(".")
    expect(cb.from_user.id, "send_to", symbol=symbol, amount=text)
    await _edit(
        cb,
        f"📤 <b>Send {text} {symbol}</b>\n\nWho to? Send a 0x address or a "
        "@username.\n\n<i>They don't need an account — if they've never used "
        "me I'll give you a link to pass on.</i>",
    )


# ── launch ──────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "menu:launch")
async def launch_start(cb: CallbackQuery) -> None:
    await cb.answer()
    await _edit(
        cb,
        "🚀 <b>Launch a token</b>\n\nHow should it trade?\n\n"
        "📈 <b>Bonding curve</b> — price starts low and climbs as people buy; "
        "it graduates to a pool later.\n"
        "⚡ <b>Instant pool</b> — a Uniswap pool from the first block, trading "
        "like any other token straight away.",
        menu.launch_menu(),
    )


@router.callback_query(F.data.startswith("lnm:venue:"))
async def launch_venue(cb: CallbackQuery) -> None:
    venue = cb.data.split(":", 2)[2]
    await cb.answer()
    expect(cb.from_user.id, "launch_name", venue=venue)
    await _edit(
        cb,
        f"🚀 <b>{'Pons' if venue == 'pons' else 'pools.trade'}</b>\n\n"
        "Send me the ticker and the name:\n<code>SLR Solar Token</code>\n\n"
        "<i>Reply to a photo with it and that picture becomes the token's "
        "image.</i>",
    )


# ── ask ─────────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "menu:ask")
async def ask_start(cb: CallbackQuery) -> None:
    await cb.answer()
    await _edit(
        cb,
        "💬 <b>Ask me anything</b>\n\nJust type it — no command needed:\n\n"
        "<i>\"is NVDA worth holding here?\"</i>\n"
        "<i>\"what can I earn on USDG?\"</i>\n"
        "<i>\"analyse 0xabc…\"</i>",
        menu.grid([], back="home:refresh"),
    )


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
    """The one piece a button couldn't offer: an amount, a recipient, a ticker
    we don't hold yet, a token's name.

    Registered ahead of chat's catch-all, so it must hand back anything it
    wasn't waiting for — otherwise every question to OPRAI would be read as
    whatever the last menu asked for.
    """
    from aiogram.dispatcher.event.bases import SkipHandler

    waiting = expects(message.from_user.id)
    if waiting is None:
        raise SkipHandler

    text = (message.text or "").strip()
    kind = waiting["kind"]
    who = message.from_user.id

    def amount_or_none(raw: str) -> Decimal | None:
        try:
            value = Decimal(raw.replace(",", "."))
            return value if value > 0 else None
        except Exception:  # noqa: BLE001 — anything unparseable is not a number
            return None

    # ── swap ────────────────────────────────────────────────────────────────
    if kind in ("swap_from", "swap_to"):
        clear_expect(who)
        draft = _draft_for(who)
        draft["from" if kind == "swap_from" else "to"] = text.upper().lstrip("$")
        if draft["from"] and draft["to"]:
            await message.answer(
                f"💱 <b>{draft['from']} → {draft['to']}</b>\n\nHow much "
                f"{draft['from']}?",
                reply_markup=menu.amount_menu(draft["from"]),
            )
        else:
            tokens, _ = await _holdings(who)
            await message.answer(
                f"💱 <b>Swap {draft['from']} →</b>\n\nWhat do you want?",
                reply_markup=menu.buy_menu(draft["from"] or "", tokens),
            )
        return

    if kind == "swap_amount":
        amount = amount_or_none(text)
        if amount is None:
            await message.answer("That doesn't look like an amount — try again.")
            return
        clear_expect(who)
        from app.handlers.swap import swap_cmd

        await swap_cmd(message, _ArgString(
            f"{_plain(amount)} {waiting['from']} {waiting['to']}"))
        return

    # ── bridge ──────────────────────────────────────────────────────────────
    if kind == "bridge_amount":
        amount = amount_or_none(text)
        if amount is None:
            await message.answer("That doesn't look like an amount — try again.")
            return
        clear_expect(who)
        from app.handlers.bridge import bridge_cmd

        await bridge_cmd(message, _ArgString(
            f"{_plain(amount)} ETH from {waiting['chain']}"))
        return

    # ── send ────────────────────────────────────────────────────────────────
    if kind == "send_amount":
        amount = amount_or_none(text)
        if amount is None:
            await message.answer("That doesn't look like an amount — try again.")
            return
        expect(who, "send_to", symbol=waiting["symbol"], amount=_plain(amount))
        await message.answer(
            f"📤 <b>Send {_plain(amount)} {waiting['symbol']}</b>\n\nWho to? "
            "Send a 0x address or a @username."
        )
        return

    if kind == "send_to":
        clear_expect(who)
        from app.handlers.send import send_cmd

        await send_cmd(message, _ArgString(
            f"{waiting['amount']} {waiting['symbol']} {text}"))
        return

    # ── launch ──────────────────────────────────────────────────────────────
    if kind == "launch_name":
        clear_expect(who)
        from app.handlers.launch import launch_cmd

        await launch_cmd(message, _ArgString(text))
        return

    raise SkipHandler


def _plain(amount: Decimal) -> str:
    return f"{amount:f}".rstrip("0").rstrip(".")
