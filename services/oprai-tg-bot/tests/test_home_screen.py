"""The home screen — /start as a screen rather than a manual.

The hazard with buttons is that they invoke handlers written for typed
commands. A handler that decides what to do by reading `message.text` sees the
home card instead of "/lend" and quietly does something else — which is how a
tap on "Lend & borrow" became a withdrawal.
"""

from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.handlers import home


def test_every_button_goes_somewhere():
    """A callback with no handler is a button that does nothing when tapped."""
    known = set(home.READS) | set(home.PROMPTS) | {"refresh", "help"}
    for row in home.keyboard().inline_keyboard:
        for button in row:
            what = button.callback_data.split(":", 1)[1]
            assert what in known, f"{button.text} has no handler ({what})"


def test_read_buttons_name_functions_that_exist():
    """A typo here is a button that raises the moment someone presses it."""
    for what, (module_name, func_name) in home.READS.items():
        module = __import__(f"app.handlers.{module_name}", fromlist=[func_name])
        assert hasattr(module, func_name), f"{what} -> {module_name}.{func_name} missing"


def test_a_handler_that_reads_message_text_still_behaves_from_a_button():
    """/lend, /borrow, /repay and /withdraw share one handler that picks the
    verb out of the message. From a button that text is the home card, and the
    first word is an emoji — which used to fall through to the withdraw
    branch."""
    from app.handlers.lend import lend_router
    import inspect

    source = inspect.getsource(lend_router)
    assert 'verb not in ("lend", "borrow", "repay", "withdraw")' in source, (
        "the verb is taken from message.text without a fallback"
    )


def test_prompts_show_a_real_example_not_a_grammar():
    """Angle brackets make someone decode a spec. An example they can copy is
    the whole point of the button."""
    for what, text in home.PROMPTS.items():
        # Either a command to copy, or — for asking a question, where there is
        # no command — a literal example of what to type.
        assert "<code>/" in text or "<i>\"" in text, (
            f"{what} prompt gives nothing to copy"
        )
        assert "&lt;" not in text, f"{what} prompt shows placeholders, not an example"


@pytest.mark.asyncio
async def test_the_card_leads_with_the_balance_and_names_the_wallet():
    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "home_card")
    try:
        text = await home.card(tg)
        assert "ETH" in text.split("\n")[2], "the balance is not the first thing shown"
        assert "0x" in text, "the address is missing"
        # A failing provider costs its own line, never the screen.
        assert text.startswith("👋")
    finally:
        await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()
