"""Per-wallet LLM cost cap.

Prevents a single wallet from exhausting the OpenAI budget through agentic
loops, malicious clients, or runaway integrations.

Two units cooperate:

* **Token cap** (`OPRAI_LLM_DAILY_TOKEN_CAP`, default 1M) — model-agnostic, the
  authoritative stop. `record_usage()` adds to the counter; the next message
  asserts it.
* **Message cap** (`OPRAI_LLM_DAILY_MESSAGE_CAP`, default 0 = disabled) — a
  cheap pre-call gate based purely on message count, applied even before the
  first token has been spent. Set this when you can't reliably observe
  per-message token usage (e.g. when the streaming path doesn't expose it).

Storage: Redis. Keys per (wallet, UTC date), TTL 25 hours so stale rows
expire without a sweep job. Failing open if Redis is down — the cap is a
backstop, not the only line of defence (the rate limiter still throttles).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from app.services.cache import get_cache_service

logger = logging.getLogger(__name__)


# Default cap: 1M tokens / wallet / UTC day. At gpt-4o-mini rates (≈$0.15/M
# input, $0.60/M output) that's roughly $0.40 of OpenAI spend per wallet per
# day — sized for a heavy real user, abusive enough to flag a runaway loop.
_DEFAULT_DAILY_TOKEN_CAP = 1_000_000


def _daily_token_cap() -> int:
    raw = os.getenv("OPRAI_LLM_DAILY_TOKEN_CAP", "")
    try:
        n = int(raw) if raw else _DEFAULT_DAILY_TOKEN_CAP
    except ValueError:
        n = _DEFAULT_DAILY_TOKEN_CAP
    if n < 0:
        n = 0
    return n


def _daily_message_cap() -> int:
    raw = os.getenv("OPRAI_LLM_DAILY_MESSAGE_CAP", "")
    try:
        n = int(raw) if raw else 0  # 0 = disabled
    except ValueError:
        n = 0
    if n < 0:
        n = 0
    return n


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _token_key(wallet: str) -> str:
    return f"llm:daily:tok:{wallet}:{_today()}"


def _msg_key(wallet: str) -> str:
    return f"llm:daily:msg:{wallet}:{_today()}"


class LLMCapExceeded(Exception):
    """Raised when a wallet's daily LLM cap has been hit."""

    def __init__(self, wallet: str, used: int, cap: int, unit: str) -> None:
        self.wallet = wallet
        self.used = used
        self.cap = cap
        self.unit = unit
        super().__init__(
            f"Daily LLM {unit} cap reached for wallet {wallet[:8]}…: "
            f"{used:,} / {cap:,} {unit} used"
        )


async def _read_int(cache_key: str, redis_key: str) -> int:
    cache = await get_cache_service()
    raw = await cache.get(cache_key, redis_key)
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


async def _add_int(cache_key: str, redis_key: str, delta: int) -> None:
    cache = await get_cache_service()
    prev = await _read_int(cache_key, redis_key)
    await cache.set(cache_key, str(prev + delta), redis_key)


async def assert_under_cap(wallet: str) -> None:
    """Raise `LLMCapExceeded` if the wallet has hit either daily cap.

    Both caps are checked; the first one to fail wins. Cap == 0 means
    unlimited (matches the spending-limit convention).
    """
    if not wallet:
        return

    msg_cap = _daily_message_cap()
    if msg_cap > 0:
        try:
            used = await _read_int("session:llm_daily", _msg_key(wallet))
        except Exception:  # pragma: no cover — fail open
            used = 0
        if used >= msg_cap:
            raise LLMCapExceeded(wallet=wallet, used=used, cap=msg_cap, unit="messages")

    token_cap = _daily_token_cap()
    if token_cap > 0:
        try:
            used = await _read_int("session:llm_daily", _token_key(wallet))
        except Exception:  # pragma: no cover — fail open
            used = 0
        if used >= token_cap:
            raise LLMCapExceeded(wallet=wallet, used=used, cap=token_cap, unit="tokens")


async def record_message(wallet: str) -> None:
    """Increment the wallet's daily message counter by one. Best-effort."""
    if not wallet:
        return
    try:
        await _add_int("session:llm_daily", _msg_key(wallet), 1)
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("record_message failed for %s: %r", wallet[:8], exc)


async def record_tokens(wallet: str, tokens: int) -> None:
    """Add `tokens` to the wallet's daily token counter. Best-effort."""
    if not wallet or tokens <= 0:
        return
    try:
        await _add_int("session:llm_daily", _token_key(wallet), tokens)
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("record_tokens failed for %s: %r", wallet[:8], exc)
