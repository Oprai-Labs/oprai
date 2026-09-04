"""Pasting a key after tapping Import.

A button that then demands a typed command is a button that did nothing, so
the tap arms a short window and the next message IS the key.

The hazard is what that costs everything else: this handler sits on the wallet
router, which runs before chat's catch-all. If it swallowed ordinary messages,
every question to OPRAI would vanish into the wallet importer.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from aiogram import Dispatcher, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Chat, Message, Update, User

from app.handlers.wallet import _looks_like_a_key


@pytest.mark.parametrize(
    "text, expected",
    [
        ("0x4c0883a69102937d6231471b5dbb6204fe512961708279f2e3e8a5d4b8f43a01", True),
        ("4c0883a69102937d6231471b5dbb6204fe512961708279f2e3e8a5d4b8f43a01", True),
        # An address is 20 bytes, not 32 — importing one would fail confusingly.
        ("0xb0E580Cf95E2B045b99b31ddF3137D3D88d55b8E", False),
        ("how is NVDA doing?", False),
        ("0x1234", False),
        ("0xzzzz83a69102937d6231471b5dbb6204fe512961708279f2e3e8a5d4b8f43a01", False),
    ],
)
def test_only_a_real_key_is_treated_as_one(text, expected):
    assert _looks_like_a_key(text) is expected


@pytest.mark.asyncio
async def test_an_ordinary_message_still_reaches_the_chat_handler():
    """The importer runs before chat's catch-all. Getting the fall-through
    wrong would send every question into the wallet importer instead of to
    OPRAI — so this exercises the real dispatcher, not the predicate."""
    reached: list[str] = []
    wallet_like = Router(name="wallet_like")
    chat_like = Router(name="chat_like")

    @wallet_like.message(F.text & F.chat.type.in_({"private"}))
    async def importer(message: Message) -> None:
        if not _looks_like_a_key(message.text or ""):
            raise SkipHandler
        reached.append("wallet")

    @chat_like.message(F.text)
    async def assistant(message: Message) -> None:
        reached.append("chat")

    dp = Dispatcher()
    dp.include_router(wallet_like)
    dp.include_router(chat_like)
    bot = SimpleNamespace(id=1)

    async def send(text: str) -> list[str]:
        reached.clear()
        message = Message(
            message_id=1, date=datetime.now(), chat=Chat(id=7, type="private"),
            from_user=User(id=7, is_bot=False, first_name="x"), text=text,
        )
        await dp.feed_update(bot=bot, update=Update(update_id=1, message=message))
        return list(reached)

    key = "0x" + "4c0883a69102937d6231471b5dbb6204fe512961708279f2e3e8a5d4b8f43a01"
    assert await send(key) == ["wallet"]
    assert await send("what can I earn on USDG?") == ["chat"], (
        "an ordinary question was swallowed by the wallet importer"
    )


def test_the_paste_window_is_short_and_single_use():
    """A key pasted days later, or a second one, must not be picked up by a tap
    nobody remembers making."""
    from app.handlers import wallet

    assert wallet.IMPORT_WINDOW_SECONDS <= 600
    import inspect

    source = inspect.getsource(wallet.catch_pasted_key)
    assert "_awaiting_import.pop" in source, "the window is never cleared"
    assert "time.monotonic() > until" in source, "an expired window still imports"
