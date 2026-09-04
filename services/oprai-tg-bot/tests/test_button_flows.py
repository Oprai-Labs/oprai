"""Menus that open menus, and the two ways they can go wrong.

A flow keeps state per person and registers a text handler ahead of chat's
catch-all. So: state that never expires means an amount typed tomorrow gets
swallowed by a swap nobody remembers starting, and a handler that forgets to
hand back means every question to OPRAI is read as a swap amount.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from aiogram import Dispatcher, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Chat, Message, Update, User

from app.handlers import flows, menu


# ── the grid ────────────────────────────────────────────────────────────────
def test_every_home_button_has_a_destination():
    known_prefixes = ("home:", "menu:")
    for row in menu.home_keyboard().inline_keyboard:
        for b in row:
            assert b.callback_data.startswith(known_prefixes), (
                f"{b.text} goes nowhere ({b.callback_data})"
            )


def test_a_submenu_can_always_be_left():
    """A menu you can only escape by scrolling up and retyping a command is a
    dead end."""
    for keyboard in (
        menu.sell_menu([{"symbol": "NVDA", "display": "5"}], Decimal("1")),
        menu.buy_menu("ETH", []),
        menu.amount_menu("ETH"),
    ):
        labels = [b.text for row in keyboard.inline_keyboard for b in row]
        assert any("Back" in label for label in labels), "no way back"


def test_only_tokens_they_hold_are_offered_to_sell():
    """Offering a token someone doesn't have is a choice that fails two taps
    later."""
    keyboard = menu.sell_menu([{"symbol": "NVDA", "display": "5"}], Decimal("0"))
    labels = " ".join(b.text for row in keyboard.inline_keyboard for b in row)
    assert "NVDA" in labels
    assert "ETH ·" not in labels, "ETH offered with a zero balance"


# ── Max ─────────────────────────────────────────────────────────────────────
def test_max_leaves_enough_for_gas():
    """A Max that spends the last wei leaves nothing to pay the transaction
    with, and fails on submit — the one thing a Max button must never do."""
    balance = Decimal("0.0077")
    spend = menu.percent_of(balance, 100, leave_gas=True)
    assert spend < balance
    assert balance - spend >= menu.GAS_RESERVE_ETH


def test_max_on_a_token_spends_all_of_it():
    """Gas is paid in ETH, so a token swap has no reason to hold any back."""
    assert menu.percent_of(Decimal("100"), 100, leave_gas=False) == Decimal("100")


def test_a_balance_too_small_to_cover_gas_offers_nothing():
    assert menu.percent_of(Decimal("0.0001"), 100, leave_gas=True) == 0


# ── the text handler that sits in front of chat ─────────────────────────────
@pytest.mark.asyncio
async def test_a_question_is_not_read_as_a_swap_amount():
    """The flow's text handler runs before chat's catch-all. If it kept
    messages it wasn't waiting for, asking OPRAI anything would disappear into
    a half-finished swap."""
    reached: list[str] = []
    flow_like = Router(name="flow_like")
    chat_like = Router(name="chat_like")

    @flow_like.message(F.text & ~F.text.startswith("/"))
    async def waiting(message: Message) -> None:
        if flows.expects(message.from_user.id) is None:
            raise SkipHandler
        reached.append("flow")

    @chat_like.message(F.text)
    async def assistant(message: Message) -> None:
        reached.append("chat")

    dp = Dispatcher()
    dp.include_router(flow_like)
    dp.include_router(chat_like)
    bot = SimpleNamespace(id=1)
    who = 4242

    async def send(text: str) -> list[str]:
        reached.clear()
        message = Message(
            message_id=1, date=datetime.now(), chat=Chat(id=who, type="private"),
            from_user=User(id=who, is_bot=False, first_name="x"), text=text,
        )
        await dp.feed_update(bot=bot, update=Update(update_id=1, message=message))
        return list(reached)

    flows.clear_expect(who)
    assert await send("what can I earn on USDG?") == ["chat"]

    flows.expect(who, "swap_amount", **{"from": "ETH", "to": "USDG"})
    assert await send("0.05") == ["flow"]

    flows.clear_expect(who)
    assert await send("and what about TSLA?") == ["chat"]


def test_what_the_bot_is_waiting_for_expires():
    """Otherwise a number typed tomorrow is spent on a swap nobody remembers
    starting."""
    who = 777
    flows.expect(who, "swap_amount", **{"from": "ETH", "to": "USDG"})
    assert flows.expects(who) is not None
    flows._expecting[who]["expires"] = 0  # as if the window had passed
    assert flows.expects(who) is None, "an expired prompt still captures input"


# ── no button may be a dead end ─────────────────────────────────────────────
def _callbacks(keyboard) -> list[str]:
    return [b.callback_data for row in keyboard.inline_keyboard for b in row]


def test_no_button_in_any_menu_is_dead():
    """Launch, Send, Bridge and Ask were rendered with `menu:` callbacks that
    nothing handled — tapping them did nothing at all, silently. Every
    callback a keyboard produces must be claimed by a handler somewhere."""
    import inspect

    from app.handlers import flows, home, wallet

    handled = " ".join(
        inspect.getsource(module)
        for module in (flows, home, wallet)
    )

    keyboards = [
        menu.home_keyboard(),
        menu.sell_menu([{"symbol": "NVDA", "display": "5"}], Decimal("1")),
        menu.buy_menu("ETH", []),
        menu.amount_menu("ETH"),
        menu.bridge_menu(),
        menu.bridge_amount_menu("base"),
        menu.send_token_menu([{"symbol": "NVDA", "display": "5"}], Decimal("1")),
        menu.send_amount_menu(),
        menu.launch_menu(),
    ]

    dead = []
    for keyboard in keyboards:
        for data in _callbacks(keyboard):
            prefix = data.split(":", 1)[0]
            # Either an exact match, or a handler keyed on the prefix.
            if f'"{data}"' in handled or f'startswith("{prefix}:' in handled:
                continue
            dead.append(data)
    assert not dead, "buttons that do nothing when tapped: " + ", ".join(sorted(set(dead)))


def test_every_menu_the_flows_open_has_a_reply_branch():
    """A menu that asks for something typed and has no branch to catch it
    leaves the person stuck, with their answer going to the assistant."""
    import inspect

    from app.handlers import flows

    asked = set()
    for line in inspect.getsource(flows).splitlines():
        if "expect(" in line and '"' in line and "clear_expect" not in line:
            parts = [p for p in line.split('"') if p]
            for p in parts:
                if p.replace("_", "").isalpha() and "_" in p:
                    asked.add(p)

    reply_source = inspect.getsource(flows.catch_expected_reply)
    for kind in asked:
        assert f'"{kind}"' in reply_source, f"nothing catches the answer to {kind}"


# ── never offer what you cannot deliver ─────────────────────────────────────
@pytest.mark.asyncio
async def test_every_token_the_menu_suggests_can_actually_be_resolved():
    """The buy menu listed USDe, and picking it failed with "I don't know a
    token called USDe" — the suggestion list and the token registry had
    drifted apart. A choice that fails two taps later is worse than not
    offering it."""
    from app.db import close_pool, init_pool
    from app.services import tokens as tok

    await init_pool()
    try:
        missing = []
        for symbol in menu.COMMON_TARGETS:
            if symbol.upper() == "ETH":
                continue  # native, never in the registry
            if not await tok.resolve(symbol):
                missing.append(symbol)
        assert not missing, (
            "offered but unresolvable: " + ", ".join(missing)
        )
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_the_tokens_the_bot_itself_spends_are_known():
    """Credits are paid in OPRAI and Morpho takes USDe as collateral — a
    command that names a token the registry has never heard of cannot run."""
    from app.config import settings
    from app.db import close_pool, init_pool
    from app.services import tokens as tok

    await init_pool()
    try:
        assert await tok.resolve("OPRAI"), "our own token is unresolvable"
        assert await tok.resolve("USDe"), "Morpho collateral is unresolvable"
        oprai = (await tok.resolve("OPRAI"))[0]
        assert oprai["address"].lower() == settings.OPRAI_TG_TOKEN_ADDRESS.lower()
    finally:
        await close_pool()
