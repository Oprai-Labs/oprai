"""What a room may see.

A group is where a bot like this is actually used, so the default has to be
right rather than left to each handler to remember: balances, amounts, wallet
addresses and confirmation cards go to the person, and the room gets a line
saying so.

The tests drive the helper with stand-in Telegram objects, because the thing
being checked is *where* a message went, not what the network did with it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramForbiddenError

from app.handlers import privacy


class _Bot:
    def __init__(self, forbidden: bool = False):
        self.forbidden = forbidden
        self.dms: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        if self.forbidden:
            raise TelegramForbiddenError(method=None, message="can't initiate")
        self.dms.append((chat_id, text))
        return SimpleNamespace(text=text)

    async def me(self):
        return SimpleNamespace(username="Oprai_Labs_Bot")


class _Message:
    def __init__(self, chat_type: str, bot: _Bot, user_id: int = 42):
        self.chat = SimpleNamespace(type=chat_type, id=-100)
        self.from_user = SimpleNamespace(id=user_id, username="someone")
        self.bot = bot
        self.answers: list[str] = []
        self.replies: list[str] = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        return SimpleNamespace(text=text)

    async def reply(self, text, **kwargs):
        self.replies.append(text)
        return SimpleNamespace(text=text)


SECRET = "You hold 12.5 USDG. Confirm swapping 5 NVDA from 0xabc…?"


@pytest.mark.asyncio
async def test_a_private_chat_is_answered_where_it_was_asked():
    bot = _Bot()
    msg = _Message("private", bot)
    await privacy.private_answer(msg, SECRET)
    assert msg.answers == [SECRET]
    assert bot.dms == [], "a DM was sent for a chat that was already private"


@pytest.mark.asyncio
async def test_a_group_never_sees_the_amount_or_the_wallet():
    bot = _Bot()
    msg = _Message("supergroup", bot)
    await privacy.private_answer(msg, SECRET)

    assert bot.dms and bot.dms[0][0] == 42, "the content did not reach the person"
    assert msg.answers == [], "the content was posted into the room"
    # The room gets a note, and the note carries none of the detail.
    assert msg.replies and SECRET not in msg.replies[0]
    assert "USDG" not in msg.replies[0] and "0xabc" not in msg.replies[0]


@pytest.mark.asyncio
async def test_someone_who_never_started_the_bot_is_told_how_to_fix_it():
    """Telegram will not let a bot speak first. Without this the answer
    vanishes and the person is left staring at a command that did nothing."""
    bot = _Bot(forbidden=True)
    msg = _Message("group", bot)
    out = await privacy.private_answer(msg, SECRET)

    assert out is None, "callers must be able to tell the message never landed"
    assert msg.replies and "start" in msg.replies[0].lower()
    assert SECRET not in msg.replies[0]


@pytest.mark.asyncio
async def test_a_send_failure_does_not_leak_into_the_room_either():
    class _Broken(_Bot):
        async def send_message(self, *a, **k):
            raise RuntimeError("network")

    msg = _Message("group", _Broken())
    assert await privacy.private_answer(msg, SECRET) is None
    assert msg.answers == []
    assert SECRET not in "".join(msg.replies)


def test_group_detection_covers_both_kinds_of_group():
    for kind in ("group", "supergroup"):
        assert privacy.in_group(_Message(kind, _Bot()))
    assert not privacy.in_group(_Message("private", _Bot()))


def test_every_money_handler_answers_through_the_helper():
    """A handler that calls message.answer directly with a balance or a card
    is how this regresses — one new command and the room sees everything
    again. Confirmation cards are the tell: they carry the amounts."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "handlers"
    offenders = []
    for name in ("send", "swap", "bridge", "lend", "nft", "perps", "launch",
                 "portfolio", "wallet"):
        src = (root / f"{name}.py").read_text()
        # A reply_markup on a plain message.answer means a card went to the
        # chat the command was typed in.
        for match in re.finditer(r"message\.answer\((?:[^()]|\([^()]*\))*\)", src):
            if "reply_markup" in match.group(0):
                offenders.append(f"{name}: {match.group(0)[:60]}")
    assert not offenders, "confirmation cards posted publicly:\n" + "\n".join(offenders)
