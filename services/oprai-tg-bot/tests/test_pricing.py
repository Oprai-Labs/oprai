"""What a credit costs, and what happens when we can't tell.

The old price was a fixed number of questions per $OPRAI, which meant the
token set the price: the same pack cost twice as much after a 2x and half as
much after a dump. Credits are now priced in dollars and converted at the live
rate, so these tests are mostly about that one property holding — and about
refusing to sell rather than guessing when the rate can't be read.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.services import pricing


def _pair(price: float, liquidity: float, volume: float = 5_000.0) -> dict:
    return {"priceUsd": str(price), "liquidity": {"usd": liquidity},
            "volume": {"h24": volume}}


# ── which pool sets the price ───────────────────────────────────────────────
def test_the_deepest_pool_sets_the_price():
    """Several pairs quote the same token at different prices. The one with
    real depth is the one a trade would clear against."""
    price = pricing._deepest_price([
        _pair(0.00009895, 28_870),
        _pair(0.00500000, 1_200),     # thin pool, wildly different price
    ])
    assert price == pytest.approx(0.00009895)


def test_a_near_empty_pool_prices_nothing():
    """A dust pair's mid-price moves on a single trade — that is noise, not a
    price, and selling credits against it is selling at a made-up rate."""
    assert pricing._deepest_price([_pair(0.5, 12.0)]) is None
    assert pricing._deepest_price([]) is None


def test_a_broken_quote_is_skipped_not_crashed():
    assert pricing._deepest_price([
        {"priceUsd": None, "liquidity": {"usd": 99_999}, "volume": {"h24": 1}},
        {"priceUsd": "x", "liquidity": {"usd": 99_999}, "volume": {"h24": 1}},
        {"priceUsd": "-1", "liquidity": {"usd": 99_999}, "volume": {"h24": 1}},
        _pair(0.001, 50_000),
    ]) == pytest.approx(0.001)


def test_a_pool_that_stopped_trading_prices_nothing():
    """Deep but dead: the last price is a memory of the last trade. Converting
    a top-up at it charges whatever the token was worth whenever that was."""
    assert pricing._deepest_price([_pair(0.001, 500_000, volume=0)]) is None

    # And a live-but-smaller pool beats a dead deep one.
    assert pricing._deepest_price([
        _pair(0.001, 500_000, volume=0),
        _pair(0.002, 20_000, volume=3_242),
    ]) == pytest.approx(0.002)


# ── the dollar is the promise ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_the_dollar_price_holds_when_the_token_moves(monkeypatch):
    """The whole point of the peg: a 2x in $OPRAI must not double what a pack
    costs. The dollar figure stays; the token amount halves."""
    async def cheap():
        return 0.0001

    async def double():
        return 0.0002

    monkeypatch.setattr(pricing, "oprai_usd", cheap)
    oprai_before, usd_before, _ = await pricing.credits_cost_oprai(500)

    monkeypatch.setattr(pricing, "oprai_usd", double)
    oprai_after, usd_after, _ = await pricing.credits_cost_oprai(500)

    assert usd_before == usd_after == pytest.approx(500 * settings.OPRAI_TG_CREDIT_PRICE_USD)
    assert oprai_after == pytest.approx(oprai_before / 2)


@pytest.mark.asyncio
async def test_no_rate_means_no_sale(monkeypatch):
    """A top-up converted at a guessed rate either overcharges the buyer or
    gives credits away. Refusing is the only honest third option."""
    async def unavailable():
        raise pricing.PriceUnavailable("no price")

    monkeypatch.setattr(pricing, "oprai_usd", unavailable)
    with pytest.raises(pricing.PriceUnavailable):
        await pricing.credits_cost_oprai(500)


@pytest.mark.asyncio
async def test_an_unreachable_price_source_does_not_return_a_number():
    """Rather than fall back to a stale or default rate."""
    import httpx

    async def dead(*a, **k):
        raise httpx.ConnectError("down")

    pricing._cached, pricing._cached_at = None, 0.0
    with pytest.MonkeyPatch.context() as m:
        m.setattr(httpx.AsyncClient, "get", dead)
        with pytest.raises(pricing.PriceUnavailable):
            await pricing.oprai_usd(force=True)


# ── the packs ───────────────────────────────────────────────────────────────
def test_packs_are_priced_at_a_flat_rate_per_credit():
    sizes = pricing.packs()
    assert sizes == sorted(sizes) and sizes, "packs must be listed smallest first"
    for size in sizes:
        assert pricing.credits_cost_usd(size) == pytest.approx(
            size * settings.OPRAI_TG_CREDIT_PRICE_USD
        )


def test_a_top_up_argument_names_credits_not_tokens():
    """`/topup 300` used to mean 300 $OPRAI. It now means 300 questions —
    the thing being bought."""
    from app.handlers.chat import _parse_credits

    assert _parse_credits("300") == 300
    assert _parse_credits("1,000") == 1000
    assert _parse_credits("$500") == 500
    assert _parse_credits("0") is None
    assert _parse_credits("-5") is None
    assert _parse_credits("") is None
    assert _parse_credits("abc") is None


# ── who is tapping ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_the_admin_check_reads_the_tapper_not_the_message_sender():
    """A button tap arrives on a message the BOT sent. Reading the sender off
    that message asks Telegram whether the *bot* is an admin — which it often
    is, so a non-admin would have topped the group up (and paid for it)."""
    from types import SimpleNamespace

    from app.handlers.chat import _is_group_admin

    asked: list[int] = []

    async def get_chat_member(chat_id, user_id):
        asked.append(user_id)
        return SimpleNamespace(status="member")

    bot_id, tapper_id = 8_820_421_943, 1_170_179_961
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100_123, type="supergroup"),
        from_user=SimpleNamespace(id=bot_id),      # the bot posted the menu
        bot=SimpleNamespace(get_chat_member=get_chat_member),
    )

    await _is_group_admin(message, tapper_id)
    assert asked == [tapper_id], "membership was checked for the wrong account"
