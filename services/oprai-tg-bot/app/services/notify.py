"""Sending a message the recipient will actually get.

Telegram throttles bots — roughly thirty messages a second overall, and far
fewer to one chat. When it throttles it answers 429 with a `retry_after`, and
a caller that treats that as a failure has simply dropped the message. For a
deposit alert or a filled copy-trade, dropped means the person never learns
their money moved.

So a throttle is waited out rather than logged: it is the one error Telegram
tells us exactly how to recover from. Everything else — a blocked bot, a
deleted account — is permanent, and retrying it would only burn the quota that
the next person's message needs.
"""

from __future__ import annotations

import asyncio

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from app.logging_config import log

# A throttle Telegram asks us to wait out; beyond this something is wrong and
# holding the loop hostage costs everyone else their notifications.
MAX_WAIT_SECONDS = 30


async def send(bot, telegram_id: int, text: str, **kwargs) -> bool:
    """Deliver one notification. Returns whether it landed."""
    for attempt in range(2):
        try:
            await bot.send_message(telegram_id, text, **kwargs)
            return True
        except TelegramRetryAfter as e:
            wait = min(float(getattr(e, "retry_after", 1)), MAX_WAIT_SECONDS)
            if attempt == 0:
                log.info("notify_throttled", telegram_id=telegram_id, wait=wait)
                await asyncio.sleep(wait)
                continue
            log.warning("notify_dropped_throttled", telegram_id=telegram_id)
            return False
        except TelegramForbiddenError:
            # Blocked, or the account is gone. Permanent — never retry.
            log.info("notify_blocked", telegram_id=telegram_id)
            return False
        except Exception as e:  # noqa: BLE001 — one bad chat must not stop a batch
            log.warning("notify_failed", telegram_id=telegram_id, error=str(e)[:160])
            return False
    return False
