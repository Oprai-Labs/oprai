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


# ── an address inside a sentence ────────────────────────────────────────────
ADDR = "0x14369612d61e638be7bf3b0ac302728d579d33ac"


@pytest.mark.parametrize(
    "text, should_look",
    [
        (ADDR, True),                                  # on its own
        (f"{ADDR} analiz et", True),                   # how people actually write it
        (f"analyse {ADDR}", True),
        (f"{ADDR} güvenli mi", True),
        (f"is {ADDR} a rug?", True),
        # The phrasing that exposed the old rule: neither "launchpad" nor
        # "hacim" was on the list of words that counted as asking, so a
        # question the index answers in seven seconds took twenty-four and
        # came back "no data".
        (f"{ADDR} hangi launchpad'de basıldı, hacmi ne?", True),
        (f"{ADDR} kaç holder var", True),
        (f"{ADDR} dev kim", True),
        (f"who launched {ADDR}", True),
        # An address inside an instruction is not a question about it —
        # hijacking these would answer something nobody asked instead of
        # moving the money.
        (f"send 5 USDG to {ADDR}", False),
        (f"swap 1 ETH to {ADDR}", False),
        (f"transfer 10 NVDA {ADDR}", False),
        (f"{ADDR} adresine 10 NVDA yolla", False),
        (f"{ADDR} adresine gönder", False),
        ("how is NVDA doing?", False),                 # no address at all
    ],
)
def test_only_a_question_about_an_address_is_answered_as_one(text, should_look):
    from app.handlers.intel import wants_a_look

    assert bool(wants_a_look(text)) is should_look


def test_the_rule_is_which_ones_are_instructions_not_which_are_questions():
    """Listing the ways people ask is a list that is always one phrasing
    short. Only the instructions are enumerated; everything else carrying an
    address is a question about it."""
    from app.handlers import intel

    assert not hasattr(intel, "LOOK_WORDS"), (
        "back to enumerating the ways people ask — that list can't be complete"
    )
    assert intel.ACTION_WORDS


def test_the_address_is_pulled_out_of_the_sentence():
    """The regex used to require the whole message BE the address, so
    "0x… analiz et" fell through to the model — thirty seconds to reach data
    we read in five, and sometimes it gave up."""
    from app.handlers.intel import wants_a_look

    assert wants_a_look(f"{ADDR} analiz et") == ADDR
    assert wants_a_look(f"  analyse   {ADDR}  ") == ADDR


# ── a blip is not an outage ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_dropped_connection_is_retried_once():
    """A connection reset — a redeploy on either side, a dead keep-alive —
    became "the on-chain index isn't answering" in front of someone whose
    question we answer in five seconds."""
    import httpx

    from app.services import signals_client as sc

    calls = {"n": 0}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("connection reset")
            return httpx.Response(200, json={"status": "ok"})

    original = sc.httpx.AsyncClient
    sc.httpx.AsyncClient = _Client
    try:
        out = await sc.SignalsClient()._get("/token/0xabc")
        assert out == {"status": "ok"}
        assert calls["n"] == 2, "the blip was not retried"
    finally:
        sc.httpx.AsyncClient = original


@pytest.mark.asyncio
async def test_the_index_saying_no_is_not_retried():
    """A 500 is the index answering, not a blip — hammering it helps nobody."""
    import httpx

    from app.services import signals_client as sc

    calls = {"n": 0}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kwargs):
            calls["n"] += 1
            return httpx.Response(500, text="boom")

    original = sc.httpx.AsyncClient
    sc.httpx.AsyncClient = _Client
    try:
        with pytest.raises(sc.SignalsError):
            await sc.SignalsClient()._get("/token/0xabc")
        assert calls["n"] == 1, "a real error was retried"
    finally:
        sc.httpx.AsyncClient = original


# ── what the card must never say ────────────────────────────────────────────
def test_a_score_built_from_nothing_is_not_called_low_risk():
    """SLINK had fallen from tens of millions and the card said "6/100 — low",
    because our index had seen none of its trades: no venue, no volume, no
    ATH, no flow. The score was real; what it was made of was holders and
    supply. Saying "low" there is worse than saying nothing."""
    from app.handlers.intel import render

    blind = {"facts": {"risk_score": 6, "risk_label": "LOW", "holders": 26622,
                       "venues": [], "swaps_24h": 0, "volume_24h_usd": 0.0}}
    card = render(blind, "SLINK", "0xfa89ed9d12bf74add8253ddfaa426c4d8a0fa603")
    assert "low" not in card.lower(), card
    assert "unscored" in card.lower(), card
    assert "no trade history" in card.lower(), card


def test_a_score_with_real_trade_data_still_reads_normally():
    from app.handlers.intel import render

    seen = {"facts": {"risk_score": 6, "venues": ["uniswap"], "swaps_24h": 4210,
                      "volume_24h_usd": 80_230_000.0, "holders": 26622}}
    card = render(seen, "SLINK", "0xfa89")
    assert "Risk 6/100" in card and "low" in card.lower()


def test_the_fall_from_the_high_is_stated_plainly():
    """The single most important line on the card for a token that has round-
    tripped, and it was not on it at all."""
    from app.handlers.intel import render

    report = {"facts": {"risk_score": 40, "venues": ["uniswap"], "swaps_24h": 10,
                        "volume_24h_usd": 1.0,
                        "drawdown_from_ath": 93.4, "ath_mcap_usd": 80_000_000}}
    card = render(report, "SLINK", "0xfa89")
    assert "Down 93% from its high" in card, card
    assert "80" in card, "the peak it fell from is not shown"


def test_smart_money_selling_does_not_read_like_smart_money_buying():
    """Reporting only the holder count let a token they were dumping look
    exactly like one they were accumulating."""
    from app.handlers.intel import render

    base = {"risk_score": 20, "venues": ["uniswap"], "swaps_24h": 10,
            "volume_24h_usd": 1.0, "smart_money_holders": 101,
            "smart_money_holding_pct": 0.9}
    selling = render({"facts": {**base, "smart_money_net_usd": -250_000,
                                "smart_money_sellers": 44}}, "X", "0xa")
    buying = render({"facts": {**base, "smart_money_net_usd": 250_000}}, "X", "0xa")

    assert "net SELLING" in selling and "44 selling" in selling, selling
    assert "net buying" in buying, buying
    assert selling != buying


def test_the_days_move_sits_next_to_the_price():
    from app.handlers.intel import render

    card = render({"facts": {"risk_score": 20, "venues": ["u"], "swaps_24h": 1,
                             "volume_24h_usd": 1.0}}, "X", "0xa",
                  market={"price": "0.001", "change_24h": -71.4})
    assert "71.4%" in card and "▼" in card, card


def test_a_young_tokens_24h_gain_does_not_hide_what_it_is_doing_now():
    """SLINK showed "+1656% (24h)" while everyone who had bought that morning
    was down by half: the window reached back to its first hours. The short
    windows say whether you are catching a falling knife; the long one has to
    say what it actually spans."""
    from app.handlers.intel import render

    card = render(
        {"facts": {"risk_score": 20, "venues": ["u"], "swaps_24h": 1,
                   "volume_24h_usd": 1.0, "age_days": 1}},
        "SLINK", "0xa",
        market={"price": "0.0005", "change_1h": -8.2, "change_6h": -41.0,
                "change_24h": 1656.0},
    )
    assert "▼8.2% 1h" in card and "▼41.0% 6h" in card, card
    assert card.index("1h") < card.index("24h"), "the flattering number leads"
    assert "reaches back to launch" in card, card


def test_an_older_token_is_not_labelled_that_way():
    from app.handlers.intel import render

    card = render(
        {"facts": {"risk_score": 20, "venues": ["u"], "swaps_24h": 1,
                   "volume_24h_usd": 1.0, "age_days": 35}},
        "X", "0xa", market={"price": "1", "change_24h": 4.0},
    )
    assert "reaches back to launch" not in card


def test_a_handful_of_trades_is_not_enough_to_score():
    """Once pool discovery was fixed the index started returning venues and
    volume for SLINK — and a market cap of minus $0.0005, because 35 swaps is
    not a market and the price derived from them is wrong. Having data is not
    the same as having enough of it."""
    from app.handlers.intel import render

    card = render({"facts": {"risk_score": 6, "venues": [{"dex": "uniswap-v4"}],
                             "swaps_24h": 35, "volume_24h_usd": 3863.38,
                             "ath_mcap_usd": -0.00048,
                             "drawdown_from_ath": 0.015}}, "SLINK", "0xa")
    assert "unscored" in card.lower(), card
    assert "35 trades" in card, card
    assert "from its high" not in card, "an impossible high was still shown"


def test_a_real_market_is_scored_and_its_drawdown_shown():
    from app.handlers.intel import render

    card = render({"facts": {"risk_score": 12, "venues": [{"dex": "uniswap-v4"}],
                             "swaps_24h": 46134, "volume_24h_usd": 9_000_000.0,
                             "ath_mcap_usd": 296_955_250,
                             "drawdown_from_ath": 15.6}}, "CASHCAT", "0xb")
    assert "Risk 12/100" in card
    assert "Down 16% from its high" in card, card
    assert "$296.96M" in card or "$297" in card, card


def test_a_negative_high_is_never_printed():
    """The index returns these for thinly-traded tokens. Rendering one would
    put "Down 81% from its high (-$93.5K)" on the card."""
    from app.handlers.intel import render

    card = render({"facts": {"risk_score": 30, "venues": [{"dex": "u"}],
                             "swaps_24h": 5000, "volume_24h_usd": 1e6,
                             "ath_mcap_usd": -93_546.78,
                             "drawdown_from_ath": 0.807}}, "OPRAI", "0xc")
    assert "from its high" not in card, card
    assert "-$" not in card, card
