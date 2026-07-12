"""Unit tests for the keyword-augmentation layer of intent_router.

These exercise the pure-Python word-boundary matcher only — no OpenAI call,
no chat service required. They guard against substring false-positives
(e.g. "database" triggering Base chain) and false-negatives that hid the
cross-chain detection bug seen in the screenshot.
"""

from __future__ import annotations

import pytest

from app.services.intent_router import _augment_protocols_from_keywords


# ─── Cross-chain (relay) — the bug from the screenshot ───────────────────────

class TestCrossChainDetection:
    """A non-Solana chain mention or bridge verb must force `relay`."""

    def test_screenshot_query_turkish_base_eth(self):
        # Exact failing message from the bug report.
        msg = "1 sol karşılığı base ağında ethereum al"
        assert "relay" in _augment_protocols_from_keywords(msg, ())

    def test_turkish_bridge_verb(self):
        for msg in [
            "1 SOL'u köprüle",
            "USDC köprüleme yap",
            "ethereum'a köprü kur",
        ]:
            assert "relay" in _augment_protocols_from_keywords(msg, ()), msg

    def test_english_bridge_verb(self):
        for msg in [
            "bridge 1 SOL to ETH",
            "do a cross-chain swap",
            "cross chain transfer",
        ]:
            assert "relay" in _augment_protocols_from_keywords(msg, ()), msg

    @pytest.mark.parametrize("chain", [
        "ethereum", "arbitrum", "optimism", "polygon", "avalanche",
        "linea", "scroll", "zksync", "celo", "fantom",
        "base", "bsc", "evm",
    ])
    def test_non_solana_chain_implies_relay(self, chain):
        msg = f"swap 1 SOL to USDC on {chain}"
        assert "relay" in _augment_protocols_from_keywords(msg, ()), chain

    def test_wormhole_routes_under_relay(self):
        # Wormhole has no canonical id; piggyback on relay so crosschain.txt loads.
        assert "relay" in _augment_protocols_from_keywords(
            "use wormhole to send 1 SOL to ethereum", ()
        )

    def test_mayan_routes_under_relay(self):
        assert "relay" in _augment_protocols_from_keywords(
            "bridge via mayan", ()
        )


# ─── No false positives — word boundary correctness ──────────────────────────

class TestNoFalsePositives:
    """Word-boundary matching must not catch substrings inside larger words."""

    def test_database_does_not_trigger_relay(self):
        assert "relay" not in _augment_protocols_from_keywords(
            "show me the database schema", ()
        )

    def test_based_does_not_trigger_relay(self):
        assert "relay" not in _augment_protocols_from_keywords(
            "this trade is based on a signal", ()
        )

    def test_baseline_does_not_trigger_relay(self):
        assert "relay" not in _augment_protocols_from_keywords(
            "what's my baseline portfolio value", ()
        )

    def test_scrollbar_does_not_trigger_relay(self):
        assert "relay" not in _augment_protocols_from_keywords(
            "scrollbar is broken in the UI", ()
        )

    def test_pure_solana_swap_no_relay(self):
        # No chain mention, no bridge verb, no provider mention → not cross-chain.
        assert "relay" not in _augment_protocols_from_keywords(
            "swap 1 SOL for USDC on Jupiter", ()
        )


# ─── Existing protocols still work after the regex switch ────────────────────

class TestExistingKeywordsStillWork:
    """Smoke tests that the substring→regex migration didn't regress anything."""

    @pytest.mark.parametrize("msg,proto", [
        ("provide liquidity to a DLMM pool", "meteora"),
        ("use Whirlpool", "orca"),
        ("buy on CLMM", "raydium"),
        ("deposit into K-Lend", "kamino"),
        ("stake to jitoSOL", "jito"),
        ("stake via mSOL", "marinade"),
        ("launch on pump.fun", "pumpfun"),
        ("MMM pool listing", "magic_eden"),
        ("squid router bridge", "squid"),
        ("debridge swap", "debridge"),
    ])
    def test_known_keyword(self, msg, proto):
        assert proto in _augment_protocols_from_keywords(msg, ()), msg

    def test_classifier_pick_is_preserved(self):
        # If the classifier already picked something, we never drop it.
        result = _augment_protocols_from_keywords("hello", ("jupiter",))
        assert "jupiter" in result

    def test_multiple_protocols_merged(self):
        result = _augment_protocols_from_keywords(
            "swap on raydium then bridge to base via relay", ()
        )
        assert "raydium" in result
        assert "relay" in result
