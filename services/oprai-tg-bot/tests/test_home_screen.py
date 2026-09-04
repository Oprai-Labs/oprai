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
    """A callback with no handler is a button that does nothing when tapped.

    Three ways a home button can be served: a read that runs a command, a
    prompt, or an explicit branch in the handler (wallet, refresh, help). The
    branches are read out of the source so removing one fails here rather than
    in someone's chat.
    """
    import inspect

    source = inspect.getsource(home.home_button)
    branches = {
        line.split('"')[1]
        for line in source.splitlines()
        if 'what == "' in line
    }
    known = set(home.READS) | set(home.PROMPTS) | branches

    for row in home.keyboard().inline_keyboard:
        for button in row:
            prefix, what = button.callback_data.split(":", 1)
            if prefix == "menu":
                continue  # opens a flow menu; covered by the flow tests
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


# ── the wallet card's own buttons ───────────────────────────────────────────
def test_the_wallet_card_offers_what_you_can_do_to_a_wallet():
    """Export and import existed as subcommands and went unnoticed — a
    subcommand you have to already know about is a feature that isn't there."""
    from app.handlers.wallet import _wallet_kb

    labels = " ".join(
        b.text.lower() for row in _wallet_kb().inline_keyboard for b in row
    )
    for expected in ("export", "import", "new", "wallets"):
        assert expected in labels, f"no way to {expected} from the wallet card"


def test_every_wallet_button_is_handled():
    import inspect

    from app.handlers import wallet as wallet_handler
    from app.handlers.wallet import _wallet_kb

    source = inspect.getsource(wallet_handler.wallet_button)
    for row in _wallet_kb().inline_keyboard:
        for button in row:
            what = button.callback_data.split(":", 1)[1]
            assert f'"{what}"' in source, f"{button.text} ({what}) does nothing"


def test_a_button_acts_for_the_person_not_the_bot():
    """A callback's message was sent by the bot, so from_user is the bot — every
    button would otherwise act on the bot's own wallet.

    And the fix has to be a COPY: aiogram's models are frozen, so assigning to
    from_user raises a ValidationError that escapes the handler and stops the
    bot processing updates at all. That shipped once."""
    import inspect

    from app.handlers import home
    from app.handlers.wallet import wallet_button

    for func in (home.home_button, wallet_button):
        source = inspect.getsource(func)
        assert "as_person(cb)" in source, f"{func.__name__} keeps the bot as the user"
        assert "from_user =" not in source, (
            f"{func.__name__} assigns to a frozen model — this takes the bot down"
        )


def test_repointing_a_message_does_not_mutate_a_frozen_model():
    """The real check: run it. A frozen-model assignment raises here rather
    than in production, where it stopped every update."""
    from datetime import datetime
    from types import SimpleNamespace

    from aiogram.types import Chat, Message, User

    from app.handlers.home import as_person

    bot_user = User(id=999, is_bot=True, first_name="Bot")
    person = User(id=42, is_bot=False, first_name="Real", username="real")
    message = Message(message_id=1, date=datetime.now(),
                      chat=Chat(id=-100, type="supergroup"),
                      from_user=bot_user, text="the home card")
    cb = SimpleNamespace(message=message, from_user=person, bot=None)

    out = as_person(cb)
    assert out.from_user.id == 42
    assert message.from_user.id == 999, "the original was mutated"
