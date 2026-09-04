"""The on-chain X-ray, read directly rather than through a model.

Asking the assistant to analyse an address worked, but it cost two LLM round
trips on top of the read — about thirty seconds against six — and it varied:
the same question sometimes came back "I couldn't fetch any data for this
address" while the index was answering perfectly.
"""

from __future__ import annotations

import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.handlers import intel


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


LIVE = _reachable(f"{settings.OPRAI_TG_SIGNER_URL.rstrip('/')}/health")

REPORT = {
    "status": "ok",
    "kpis": [
        {"label": "Holders", "value": "34058"},
        {"label": "Age (days)", "value": "35"},
        {"label": "Top-10 wallet concentration", "value": 11.7, "fmt": "%"},
        {"label": "In LP pools", "value": 22.5, "fmt": "%"},
        {"label": "Whales (>1%)", "value": 5},
    ],
    "facts": {"risk_score": 23, "launchpad": "Doppler",
              "smart_money_holders": 359, "smart_money_holding_pct": 9.6},
}


def test_a_risk_number_is_given_a_word():
    """23/100 says nothing on its own — nobody knows whether that is good."""
    assert intel._band(23) == ("🟢", "low")
    assert intel._band(45) == ("🟡", "medium")
    assert intel._band(88) == ("🔴", "high")


def test_the_card_says_what_someone_asked_for():
    out = intel.render(REPORT, "DELTA", "0x" + "ab" * 20,
                       {"price": 0.01736, "mcap": 17_360_000,
                        "liquidity": 1_090_000, "volume": 5_770_000})
    for expected in ("DELTA", "Risk 23/100", "low", "34058", "35 days",
                     "11.7%", "Doppler", "359"):
        assert expected in out, f"missing from the card: {expected}"
    # Money is readable, not raw.
    assert "$17.36M" in out and "$0.0174" in out


def test_an_index_with_no_market_data_still_renders():
    """Price comes from a different source than the X-ray. One being down
    costs four lines, not the answer."""
    out = intel.render(REPORT, "DELTA", "0x" + "ab" * 20, {})
    assert "Risk 23/100" in out and "34058" in out
    assert "Price" not in out


def test_only_a_real_address_is_treated_as_one():
    """A bare address is a request to analyse it; anything else must fall
    through to the assistant."""
    assert intel.ADDRESS.match("0x" + "a" * 40)
    assert not intel.ADDRESS.match("0x" + "a" * 39)
    assert not intel.ADDRESS.match("how is NVDA doing?")
    # A transaction hash is 32 bytes, not 20 — analysing it as a token would
    # produce a confident answer about nothing.
    assert not intel.ADDRESS.match("0x" + "a" * 64)


@pytest.mark.skipif(not LIVE, reason="signer not running")
@pytest.mark.asyncio
async def test_a_symbol_resolves_to_an_address_before_the_lookup():
    """"/token NVDA" has to become an address; the index knows nothing about
    tickers."""
    from app.services import tokens as tok

    await init_pool()
    try:
        found = await tok.resolve("NVDA")
        assert found and found[0]["address"].startswith("0x")
    finally:
        await close_pool()
