"""Conversation credits.

Credits meter the model, and only the model: asking OPRAI a question, an
analysis, a strategy. On-chain actions are never charged here — they already
pay OPRAI's trading commission, and billing an intent twice would push people
away from the assistant that makes the product worth using.

A balance belongs to a *scope*, not a person. A private chat is its own scope;
a group is one shared scope, because in a room the quota belongs to the room —
an admin tops it up once and everyone draws from it, which is how a group bot
is expected to behave.

Free and purchased credits are counted apart. The free allowance is a per
window ration: it refills on a rolling window so a quiet week restores a group
without anyone asking, and it does NOT carry over, or an idle scope would
accumulate free model calls for ever. Purchased credits are property — they
never expire and are spent only once the ration is gone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta

from app.config import settings
from app.db import pool


@dataclass
class Balance:
    scope_id: int
    is_group: bool
    free_used: int
    paid: int

    @property
    def free_left(self) -> int:
        return max(free_allowance(self.is_group) - self.free_used, 0)

    @property
    def remaining(self) -> int:
        return self.free_left + self.paid

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0


def free_allowance(is_group: bool) -> int:
    return (
        settings.OPRAI_TG_FREE_GROUP_CREDITS
        if is_group
        else settings.OPRAI_TG_FREE_USER_CREDITS
    )


def _window() -> timedelta:
    return timedelta(hours=max(settings.OPRAI_TG_FREE_WINDOW_HOURS, 1))


def _balance(row) -> Balance:
    return Balance(row["scope_id"], row["is_group"], int(row["free_used"]),
                   int(row["paid"]))


async def _ledger(scope_id: int, telegram_id: int | None, delta: int,
                  reason: str, detail: dict | None = None) -> None:
    await pool().execute(
        """
        INSERT INTO tg_credit_ledger (scope_id, telegram_id, delta, reason, detail)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        """,
        scope_id, telegram_id, delta, reason, json.dumps(detail or {}),
    )


async def balance(scope_id: int, is_group: bool) -> Balance:
    """Current balance, resetting the free ration if the window has rolled.

    The reset is written by the same statement that reads the row, so two
    messages arriving together can't both decide to refill.
    """
    row = await pool().fetchrow(
        """
        INSERT INTO tg_credits (scope_id, is_group, window_start)
        VALUES ($1, $2, now())
        ON CONFLICT (scope_id) DO UPDATE SET
            free_used = CASE
                WHEN tg_credits.window_start < now() - $3::interval THEN 0
                ELSE tg_credits.free_used
            END,
            window_start = CASE
                WHEN tg_credits.window_start < now() - $3::interval
                THEN now() ELSE tg_credits.window_start
            END,
            updated_at = now()
        RETURNING scope_id, is_group, free_used, paid, (xmax = 0) AS created
        """,
        scope_id, is_group, _window(),
    )
    if row["created"]:
        await _ledger(scope_id, None, free_allowance(is_group), "free_window",
                      {"group": is_group})
    return _balance(row)


async def spend(scope_id: int, is_group: bool, telegram_id: int,
                amount: int = 1, detail: dict | None = None) -> Balance | None:
    """Charge the scope, free ration first. Returns the new balance, or None
    if it can't pay.

    The check and the charge are one statement: two messages sent at the same
    moment must not both pass a check against the same last credit.
    """
    await balance(scope_id, is_group)  # ensure the row exists / window is fresh
    allowance = free_allowance(is_group)
    row = await pool().fetchrow(
        """
        UPDATE tg_credits
           SET free_used = free_used + LEAST($2, GREATEST($3 - free_used, 0)),
               paid      = paid - GREATEST($2 - GREATEST($3 - free_used, 0), 0),
               updated_at = now()
         WHERE scope_id = $1
           AND GREATEST($3 - free_used, 0) + paid >= $2
        RETURNING scope_id, is_group, free_used, paid
        """,
        scope_id, amount, allowance,
    )
    if row is None:
        return None
    await _ledger(scope_id, telegram_id, -amount, "spend", detail)
    return _balance(row)


async def refund(scope_id: int, telegram_id: int, amount: int = 1,
                 reason: str = "failed") -> None:
    """Give a credit back when the answer never arrived.

    A model call that errors out has given the user nothing, and charging for
    nothing is the fastest way to lose their trust in the meter. The ration is
    credited back first, mirroring how it was spent.
    """
    await pool().execute(
        """
        UPDATE tg_credits
           SET free_used = GREATEST(free_used - $2, 0),
               paid      = paid + GREATEST($2 - free_used, 0),
               updated_at = now()
         WHERE scope_id = $1
        """,
        scope_id, amount,
    )
    await _ledger(scope_id, telegram_id, amount, "refund", {"reason": reason})


async def grant(scope_id: int, is_group: bool, amount: int,
                telegram_id: int | None = None, reason: str = "topup",
                detail: dict | None = None) -> Balance:
    """Add purchased credits — an admin top-up, or a paid one. These never
    expire, so they are not touched by the window."""
    await balance(scope_id, is_group)
    row = await pool().fetchrow(
        "UPDATE tg_credits SET paid = paid + $2, updated_at = now() "
        "WHERE scope_id = $1 RETURNING scope_id, is_group, free_used, paid",
        scope_id, amount,
    )
    await _ledger(scope_id, telegram_id, amount, reason, detail)
    return _balance(row)
