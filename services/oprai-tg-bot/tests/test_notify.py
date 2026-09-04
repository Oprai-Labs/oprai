"""Delivering a notification the person will actually receive.

A deposit alert or a filled copy-trade is the only way someone learns their
money moved. Telegram throttles bots and answers 429 with a `retry_after`;
treating that as a failure drops the message silently, which is the whole
problem these tests exist to prevent.
"""

from __future__ import annotations

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from app.services import notify


class _Bot:
    def __init__(self, fail_with=None, fail_times: int = 0):
        self.fail_with = fail_with
        self.fail_times = fail_times
        self.sent: list[tuple[int, str]] = []
        self.attempts = 0

    async def send_message(self, chat_id, text, **kwargs):
        self.attempts += 1
        if self.fail_with and self.attempts <= self.fail_times:
            raise self.fail_with
        self.sent.append((chat_id, text))


@pytest.mark.asyncio
async def test_a_throttled_message_is_waited_out_not_dropped(monkeypatch):
    slept: list[float] = []

    async def no_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(notify.asyncio, "sleep", no_sleep)
    bot = _Bot(TelegramRetryAfter(method=None, message="flood", retry_after=3), 1)

    assert await notify.send(bot, 1, "your deposit landed") is True
    assert bot.sent, "the message was dropped instead of retried"
    assert slept == [3], "Telegram told us how long to wait and we ignored it"


@pytest.mark.asyncio
async def test_a_wait_is_bounded(monkeypatch):
    """A single absurd retry_after must not hold the loop — and everyone
    else's notifications — hostage."""
    slept: list[float] = []

    async def no_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(notify.asyncio, "sleep", no_sleep)
    bot = _Bot(TelegramRetryAfter(method=None, message="flood", retry_after=9999), 1)

    await notify.send(bot, 1, "hi")
    assert slept == [notify.MAX_WAIT_SECONDS]


@pytest.mark.asyncio
async def test_a_blocked_user_is_not_retried():
    """Blocked is permanent. Retrying it burns the quota the next person's
    message needs."""
    bot = _Bot(TelegramForbiddenError(method=None, message="blocked"), 5)
    assert await notify.send(bot, 1, "hi") is False
    assert bot.attempts == 1


@pytest.mark.asyncio
async def test_one_bad_chat_does_not_raise_into_the_caller():
    """These run inside background loops; an exception escaping would stop the
    watcher and every later notification with it."""
    bot = _Bot(RuntimeError("network"), 5)
    assert await notify.send(bot, 1, "hi") is False


@pytest.mark.asyncio
async def test_the_happy_path_sends_once():
    bot = _Bot()
    assert await notify.send(bot, 7, "hello") is True
    assert bot.sent == [(7, "hello")] and bot.attempts == 1
