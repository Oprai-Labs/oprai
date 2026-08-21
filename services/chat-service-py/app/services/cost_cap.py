"""Per-wallet and per-chat LLM usage caps.

Three layers cooperate to keep one wallet from exhausting the OpenAI budget
or chaining a single conversation into runaway context bloat:

1. **Per-wallet daily / weekly / monthly token + message caps** — model-agnostic
   guardrails recorded against the wallet. Reset on a UTC calendar boundary
   (midnight / ISO-week / month-start).
2. **Per-chat token + message cap** — kept on `chat_sessions` so that one
   conversation cannot grow its input context indefinitely. When reached,
   the session is hard-locked (`is_locked = true`) and the user is forced
   to start a new chat. Survives reload because the lock is in Postgres.
3. **Daily message cap** — a cheap pre-call gate that does not require
   measuring tokens. Useful as a backstop while the token counter catches up.

Storage: Redis for per-wallet counters (TTLs auto-expire stale rows);
Postgres for per-chat usage and the durable lock flag.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone, UTC

from app.services.cache import get_cache_service

logger = logging.getLogger(__name__)


# ── Defaults — sized so genuine usage feels generous, abuse hits a wall ────

# Per-wallet, per-UTC-day. Sized to cover ~25-40 composite deep-dive analyses
# OR ~150-200 normal turns per day, well beyond what a human user does
# manually. The cap exists to catch agent / bot loops, not normal usage.
_DEFAULT_DAILY_TOKEN_CAP = 1_500_000
_DEFAULT_DAILY_MESSAGE_CAP = 300

# Per-wallet, per-ISO-week (Mon 00:00 UTC → next Mon 00:00 UTC).
_DEFAULT_WEEKLY_TOKEN_CAP = 7_500_000
_DEFAULT_WEEKLY_MESSAGE_CAP = 1_500

# Per-wallet, per-calendar-month.
_DEFAULT_MONTHLY_TOKEN_CAP = 25_000_000
_DEFAULT_MONTHLY_MESSAGE_CAP = 5_000

# Per-chat — once a single session hits these, it is hard-locked and the user
# must start a new conversation. The token cap is the more important of the
# two because each turn replays the full history to the LLM (so input cost
# grows superlinearly). 500K = roughly 50-80 turns of mixed conversation
# before the model's context becomes unwieldy regardless of cap.
_DEFAULT_CHAT_TOKEN_CAP = 500_000
_DEFAULT_CHAT_MESSAGE_CAP = 300


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        n = int(raw) if raw else default
    except ValueError:
        n = default
    return n if n >= 0 else 0


# Dev / eval mode: NODE_ENV=development or OPRAI_DEV_DISABLE_CAPS=1 returns
# a near-infinite cap for the per-wallet daily token check. Caps stay
# enforced in production (NODE_ENV=production). The eval suite uses
# per-row wallets which already side-step daily caps, but this also helps
# during interactive dev when the developer iterates fast.
def _is_dev_no_caps() -> bool:
    if os.getenv("OPRAI_DEV_DISABLE_CAPS", "").strip() == "1":
        return True
    return os.getenv("NODE_ENV", "").strip().lower() in ("development", "dev", "test")


_INF = 10**12  # effectively-infinite cap for dev

def _daily_token_cap() -> int:    return _INF if _is_dev_no_caps() else _env_int("OPRAI_LLM_DAILY_TOKEN_CAP",   _DEFAULT_DAILY_TOKEN_CAP)
def _daily_message_cap() -> int:  return _INF if _is_dev_no_caps() else _env_int("OPRAI_LLM_DAILY_MESSAGE_CAP", _DEFAULT_DAILY_MESSAGE_CAP)
def _weekly_token_cap() -> int:   return _INF if _is_dev_no_caps() else _env_int("OPRAI_LLM_WEEKLY_TOKEN_CAP",   _DEFAULT_WEEKLY_TOKEN_CAP)
def _weekly_message_cap() -> int: return _INF if _is_dev_no_caps() else _env_int("OPRAI_LLM_WEEKLY_MESSAGE_CAP", _DEFAULT_WEEKLY_MESSAGE_CAP)
def _monthly_token_cap() -> int:  return _INF if _is_dev_no_caps() else _env_int("OPRAI_LLM_MONTHLY_TOKEN_CAP",  _DEFAULT_MONTHLY_TOKEN_CAP)
def _monthly_message_cap() -> int:return _env_int("OPRAI_LLM_MONTHLY_MESSAGE_CAP",_DEFAULT_MONTHLY_MESSAGE_CAP)
def _chat_token_cap() -> int:     return _env_int("OPRAI_LLM_CHAT_TOKEN_CAP",     _DEFAULT_CHAT_TOKEN_CAP)
def _chat_message_cap() -> int:   return _env_int("OPRAI_LLM_CHAT_MESSAGE_CAP",   _DEFAULT_CHAT_MESSAGE_CAP)


# ── Period strings — used to bucket Redis keys ─────────────────────────────

def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")

def _this_week() -> str:
    """ISO-week format `YYYY-Www` (e.g. `2026-W20`)."""
    y, w, _ = datetime.now(UTC).isocalendar()
    return f"{y:04d}-W{w:02d}"

def _this_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


# ── Redis keys ─────────────────────────────────────────────────────────────

def _token_key(wallet: str) -> str:  return f"llm:daily:tok:{wallet}:{_today()}"
def _msg_key(wallet: str) -> str:    return f"llm:daily:msg:{wallet}:{_today()}"
def _wtok_key(wallet: str) -> str:   return f"llm:weekly:tok:{wallet}:{_this_week()}"
def _wmsg_key(wallet: str) -> str:   return f"llm:weekly:msg:{wallet}:{_this_week()}"
def _mtok_key(wallet: str) -> str:   return f"llm:monthly:tok:{wallet}:{_this_month()}"
def _mmsg_key(wallet: str) -> str:   return f"llm:monthly:msg:{wallet}:{_this_month()}"


# ── Reset-time helpers ─────────────────────────────────────────────────────

def _next_daily_reset_iso() -> str:
    now = datetime.now(UTC)
    nxt = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return nxt.isoformat().replace("+00:00", "Z")

def _next_weekly_reset_iso() -> str:
    """Next Monday 00:00 UTC. ISO weeks start on Monday (weekday=0)."""
    now = datetime.now(UTC)
    days_until_monday = (7 - now.weekday()) % 7 or 7
    nxt = (now.replace(hour=0, minute=0, second=0, microsecond=0)
           + timedelta(days=days_until_monday))
    return nxt.isoformat().replace("+00:00", "Z")

def _next_monthly_reset_iso() -> str:
    """1st of next month, 00:00 UTC."""
    now = datetime.now(UTC)
    if now.month == 12:
        nxt = now.replace(year=now.year + 1, month=1, day=1, hour=0,
                          minute=0, second=0, microsecond=0)
    else:
        nxt = now.replace(month=now.month + 1, day=1, hour=0,
                          minute=0, second=0, microsecond=0)
    return nxt.isoformat().replace("+00:00", "Z")


# Backwards-compat shim — older callers import `_next_reset_at_iso`.
def _next_reset_at_iso() -> str:
    return _next_daily_reset_iso()


# ── Exceptions ─────────────────────────────────────────────────────────────

class LLMCapExceeded(Exception):
    """Raised when a wallet's daily/weekly/monthly LLM cap has been hit."""

    def __init__(self, wallet: str, used: int, cap: int, unit: str,
                 period: str = "daily", resets_at: str | None = None) -> None:
        self.wallet = wallet
        self.used = used
        self.cap = cap
        self.unit = unit          # "messages" | "tokens"
        self.period = period      # "daily" | "weekly" | "monthly"
        self.resets_at = resets_at or _next_daily_reset_iso()
        super().__init__(
            f"{period.capitalize()} LLM {unit} cap reached for wallet "
            f"{wallet[:8]}…: {used:,} / {cap:,} {unit} (resets at {self.resets_at})"
        )

    def to_payload(self) -> dict:
        msg = str(self)
        return {
            # See ChatLockedError.to_payload — frontend gates on `error`.
            "error":     msg,
            "errorType": "llm_daily_cap",   # frontend type tag kept stable for compatibility
            "unit":      self.unit,
            "period":    self.period,
            "used":      self.used,
            "cap":       self.cap,
            "resetsAt":  self.resets_at,
            "message":   msg,
        }


class ChatLockedError(Exception):
    """Raised when the chat session itself has hit its per-chat cap.

    Distinguished from `LLMCapExceeded` because the recovery path is
    different — the user must start a NEW chat, not wait for a counter
    to reset.
    """
    def __init__(self, session_id: str, used: int, cap: int, unit: str) -> None:
        self.session_id = session_id
        self.used = used
        self.cap = cap
        self.unit = unit
        super().__init__(
            f"Chat {session_id[:8]}… reached its per-chat {unit} cap "
            f"({used:,} / {cap:,}); user must start a new conversation."
        )

    def to_payload(self) -> dict:
        msg = str(self)
        return {
            # `error` is REQUIRED: the frontend only enters its cap/limit
            # handler inside `if (parsed.error)`. Without it the event falls
            # through to the generic "couldn't generate a response" fallback.
            "error":     msg,
            "errorType": "chat_limit",
            "scope":     "chat",
            "unit":      self.unit,
            "used":      self.used,
            "cap":       self.cap,
            "message":   msg,
        }


# ── Redis read/write helpers ───────────────────────────────────────────────

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


# ── Public API ─────────────────────────────────────────────────────────────

async def assert_under_cap(wallet: str, daily_token_cap: int | None = None) -> None:
    """Raise `LLMCapExceeded` if the wallet has hit any per-wallet cap.

    Checks (in order) daily messages → weekly messages → monthly messages →
    daily tokens → weekly tokens → monthly tokens. The first one to fail
    wins; cap == 0 means unlimited.

    `daily_token_cap` is the caller-resolved tier allowance
    (`tier_config.daily_token_limit`): a higher tier buys a higher daily token
    ceiling. It replaces the flat default for the *daily tokens* check only, and
    is ignored in dev-no-caps mode so local/eval runs stay unlimited. The caller
    resolves it because it needs DB access (this module is Redis-only).
    """
    if not wallet:
        return

    # Effective daily token ceiling: tier override wins, except in dev-no-caps.
    eff_daily_token_cap = _daily_token_cap()
    if daily_token_cap and daily_token_cap > 0 and not _is_dev_no_caps():
        eff_daily_token_cap = daily_token_cap

    checks: list[tuple[str, str, str, int, str]] = [
        ("messages", "daily",   _msg_key(wallet),   _daily_message_cap(),   _next_daily_reset_iso()),
        ("messages", "weekly",  _wmsg_key(wallet),  _weekly_message_cap(),  _next_weekly_reset_iso()),
        ("messages", "monthly", _mmsg_key(wallet),  _monthly_message_cap(), _next_monthly_reset_iso()),
        ("tokens",   "daily",   _token_key(wallet), eff_daily_token_cap,    _next_daily_reset_iso()),
        ("tokens",   "weekly",  _wtok_key(wallet),  _weekly_token_cap(),    _next_weekly_reset_iso()),
        ("tokens",   "monthly", _mtok_key(wallet),  _monthly_token_cap(),   _next_monthly_reset_iso()),
    ]

    for unit, period, key, cap, resets_at in checks:
        if cap <= 0:
            continue
        try:
            used = await _read_int("session:llm_daily", key)
        except Exception:  # pragma: no cover — fail open
            used = 0
        if used >= cap:
            raise LLMCapExceeded(
                wallet=wallet, used=used, cap=cap,
                unit=unit, period=period, resets_at=resets_at,
            )


async def record_message(wallet: str) -> None:
    """Increment daily + weekly + monthly message counters by one."""
    if not wallet:
        return
    for key in (_msg_key(wallet), _wmsg_key(wallet), _mmsg_key(wallet)):
        try:
            await _add_int("session:llm_daily", key, 1)
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("record_message failed (%s) for %s: %r", key, wallet[:8], exc)


async def record_tokens(wallet: str, tokens: int) -> None:
    """Add `tokens` to daily + weekly + monthly token counters."""
    if not wallet or tokens <= 0:
        return
    for key in (_token_key(wallet), _wtok_key(wallet), _mtok_key(wallet)):
        try:
            await _add_int("session:llm_daily", key, tokens)
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("record_tokens failed (%s) for %s: %r", key, wallet[:8], exc)


async def get_usage_snapshot(wallet: str) -> dict:
    """Return a structured snapshot of all per-wallet counters.

    Shape mirrors what the Settings → Usage card renders:

        {
          "daily":   {messages:{used,cap}, tokens:{used,cap}, resetsAt},
          "weekly":  {...},
          "monthly": {...},
          "chat":    {messageCap, tokenCap},
        }
    """
    async def _u(key: str) -> int:
        try:
            return await _read_int("session:llm_daily", key)
        except Exception:
            return 0

    snap = {
        "daily": {
            "messages": {"used": await _u(_msg_key(wallet)),   "cap": _daily_message_cap()},
            "tokens":   {"used": await _u(_token_key(wallet)), "cap": _daily_token_cap()},
            "resetsAt": _next_daily_reset_iso(),
        },
        "weekly": {
            "messages": {"used": await _u(_wmsg_key(wallet)), "cap": _weekly_message_cap()},
            "tokens":   {"used": await _u(_wtok_key(wallet)), "cap": _weekly_token_cap()},
            "resetsAt": _next_weekly_reset_iso(),
        },
        "monthly": {
            "messages": {"used": await _u(_mmsg_key(wallet)), "cap": _monthly_message_cap()},
            "tokens":   {"used": await _u(_mtok_key(wallet)), "cap": _monthly_token_cap()},
            "resetsAt": _next_monthly_reset_iso(),
        },
        "chat": {
            "messageCap": _chat_message_cap(),
            "tokenCap":   _chat_token_cap(),
        },
    }
    return snap


# ── Per-chat cap helpers ───────────────────────────────────────────────────

def chat_token_cap() -> int:   return _chat_token_cap()
def chat_message_cap() -> int: return _chat_message_cap()


def chat_cap_check(message_count: int, token_total: int) -> ChatLockedError | None:
    """Return a `ChatLockedError` instance if the chat has hit either per-chat
    cap, else None. Caller decides whether to raise or pass to a stream
    event.
    """
    msg_cap = _chat_message_cap()
    tok_cap = _chat_token_cap()
    if msg_cap > 0 and message_count >= msg_cap:
        return ChatLockedError(session_id="", used=message_count, cap=msg_cap, unit="messages")
    if tok_cap > 0 and token_total >= tok_cap:
        return ChatLockedError(session_id="", used=token_total, cap=tok_cap, unit="tokens")
    return None
