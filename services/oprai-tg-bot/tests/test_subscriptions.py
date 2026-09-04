"""A paid month: when it is live, what it buys, and how it ends.

The hazards are all about time and money meeting. Renewing early must not
throw away days already paid for. A subscription must lapse on its own, with
no job that has to run. And the higher limit a subscriber gets has to be a
real limit, not an absence of one — an unmetered account is one runaway loop
away from costing more than it paid.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.services import credits, subscriptions


async def _cleanup(*scopes: int) -> None:
    await pool().execute("DELETE FROM tg_subscriptions WHERE scope_id = ANY($1)", list(scopes))
    await pool().execute("DELETE FROM tg_credits WHERE scope_id = ANY($1)", list(scopes))
    await pool().execute("DELETE FROM tg_credit_ledger WHERE scope_id = ANY($1)", list(scopes))
    await pool().execute("DELETE FROM tg_users WHERE telegram_id = ANY($1)", list(scopes))


@pytest.mark.asyncio
async def test_renewing_early_adds_to_the_end_instead_of_replacing():
    """Somebody with three weeks left pays again. Starting the new month from
    today would silently take those weeks from them."""
    await init_pool()
    tg = random.randint(10**10, 10**11)
    try:
        await upsert_tg_user(tg, f"sub_{tg}")
        first = await subscriptions.extend(tg, tg, False, wei=10**16, usd=9.99)
        second = await subscriptions.extend(tg, tg, False, wei=10**16, usd=9.99)

        assert second.months == 2
        assert float(second.paid_usd) == pytest.approx(19.98)
        # Two months bought, two months held — not one.
        assert second.expires_at > first.expires_at + timedelta(days=25), (
            "the second month replaced the first instead of extending it"
        )
    finally:
        await _cleanup(tg)
        await close_pool()


@pytest.mark.asyncio
async def test_a_subscription_lapses_because_the_clock_passes_it():
    """No flag to drift, no job that has to run on time. If the row says the
    month ended, the month ended."""
    await init_pool()
    tg = random.randint(10**10, 10**11)
    try:
        await upsert_tg_user(tg, f"lapse_{tg}")
        await subscriptions.extend(tg, tg, False, wei=10**16, usd=9.99)
        assert await subscriptions.is_live(tg)

        await pool().execute(
            "UPDATE tg_subscriptions SET expires_at = now() - interval '1 second' "
            "WHERE scope_id = $1", tg,
        )
        assert not await subscriptions.is_live(tg), "an expired month still counts"
        sub = await subscriptions.get(tg)
        assert sub.days_left == 0
    finally:
        await _cleanup(tg)
        await close_pool()


@pytest.mark.asyncio
async def test_a_subscription_raises_the_daily_limit_and_expiry_lowers_it():
    """The whole product in one test: what someone pays for is a bigger
    ration, and it goes back on its own when the month ends."""
    await init_pool()
    tg = random.randint(10**10, 10**11)
    try:
        await upsert_tg_user(tg, f"limit_{tg}")

        free = await credits.balance(tg, False)
        assert free.allowance == settings.OPRAI_TG_FREE_USER_CREDITS
        assert not free.subscribed

        await subscriptions.extend(tg, tg, False, wei=10**16, usd=9.99)
        paid = await credits.balance(tg, False)
        assert paid.allowance == settings.OPRAI_TG_SUB_DAILY_CREDITS
        assert paid.subscribed
        assert paid.allowance > free.allowance

        await pool().execute(
            "UPDATE tg_subscriptions SET expires_at = now() - interval '1 day' "
            "WHERE scope_id = $1", tg,
        )
        lapsed = await credits.balance(tg, False)
        assert lapsed.allowance == settings.OPRAI_TG_FREE_USER_CREDITS
    finally:
        await _cleanup(tg)
        await close_pool()


@pytest.mark.asyncio
async def test_a_subscriber_still_has_a_ceiling():
    """"Unlimited" is one runaway loop away from costing more than the month
    was sold for. A subscriber's limit is high enough never to be felt — the
    busiest day any real wallet has had is 60 questions — and still a limit."""
    await init_pool()
    tg = random.randint(10**10, 10**11)
    try:
        await upsert_tg_user(tg, f"ceiling_{tg}")
        await subscriptions.extend(tg, tg, False, wei=10**16, usd=9.99)

        allowance = settings.OPRAI_TG_SUB_DAILY_CREDITS
        assert allowance > 60, "a real day would hit the ceiling"

        spent = await credits.spend(tg, False, tg, allowance)
        assert spent is not None and spent.free_left == 0
        assert await credits.spend(tg, False, tg, 1) is None, (
            "a subscriber could spend past their ceiling"
        )
    finally:
        await _cleanup(tg)
        await close_pool()


@pytest.mark.asyncio
async def test_revenue_reports_what_came_in():
    """The buyback is funded from this number, so it has to count months and
    dollars, not just rows."""
    await init_pool()
    a = random.randint(10**10, 10**11)
    b = a + 1
    try:
        await upsert_tg_user(a, f"rev_a_{a}")
        await upsert_tg_user(b, f"rev_b_{b}")
        await subscriptions.extend(a, a, False, wei=4 * 10**15, usd=9.99)
        await subscriptions.extend(a, a, False, wei=4 * 10**15, usd=9.99)
        await subscriptions.extend(b, b, False, wei=4 * 10**15, usd=9.99)

        rev = await subscriptions.revenue()
        assert rev["months"] >= 3
        assert float(rev["usd"]) >= 29.97 - 0.01
        assert float(rev["eth"]) >= 0.012 - 1e-9
        assert rev["live"] >= 2
    finally:
        await _cleanup(a, b)
        await close_pool()


def test_a_month_is_worth_more_than_a_heavy_month_of_questions():
    """The point of a subscription over a meter: it must still be profitable
    for the heaviest real user, or it is a per-question price wearing a hat.

    213 questions is the heaviest month observed. At the cost we are heading
    for once the prompt is cached, that month has to fit inside the price.
    """
    heaviest_month = 213
    cost_per_question_after_caching = 0.02
    assert (heaviest_month * cost_per_question_after_caching
            < settings.OPRAI_TG_SUB_PRICE_USD), (
        "the heaviest real user would cost more than they pay"
    )


def test_the_treasury_falls_back_rather_than_paying_nobody():
    """An unset treasury address must not become a transfer to 0x0."""
    treasury = settings.treasury_wallet()
    assert treasury.startswith("0x") and len(treasury) == 42
    assert int(treasury, 16) != 0
