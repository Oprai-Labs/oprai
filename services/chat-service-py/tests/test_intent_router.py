"""Unit tests for the keyword-augmentation layer of intent_router.

These exercise the pure-Python word-boundary matcher only — no OpenAI call, no
chat service required.

What that matcher is for, and what it deliberately is not: it recognises
PRODUCT and PROTOCOL NAMES ("whirlpool", "K-Lend", "pump.fun", "wormhole") so a
classifier that missed one still gets it. It does NOT recognise verbs
("bridge", "swap"), chain names ("ethereum", "base") or any language's word for
a concept — those carry meaning rather than a name, and interpreting meaning is
the classifier's job, in whatever language the user wrote.

That boundary is the thing these tests protect. An earlier version of this file
asserted the opposite — that "bridge", "köprüle" and thirteen chain names all
routed to relay through this function — and had been failing ever since the
matcher was narrowed to names. Seventeen permanently-red tests are worse than
none: they train everyone to skip the file, which is exactly where a real
regression would sit unnoticed.
"""

from __future__ import annotations

import pytest

from app.services.intent_router import _augment_protocols_from_keywords

# ─── Names are matched; meaning is not ──────────────────────────────────────

class TestNamesMatchMeaningDoesNot:
    """Product names route. Verbs, chain names and concept words do not."""

    @pytest.mark.parametrize("msg,proto", [
        ("use wormhole to send 1 SOL to ethereum", "relay"),
        ("bridge via mayan", "relay"),
        ("debridge swap", "debridge"),
    ])
    def test_bridge_product_names_route(self, msg, proto):
        # Wormhole and Mayan have no canonical id of their own; they piggyback
        # on relay so crosschain.txt loads.
        assert proto in _augment_protocols_from_keywords(msg, ()), msg

    @pytest.mark.parametrize("verb", [
        "bridge 1 SOL to ETH",
        "do a cross-chain swap",
        "cross chain transfer",
        "1 SOL'u köprüle",
        "USDC köprüleme yap",
        "haz un puente de 1 SOL",
    ])
    def test_bridge_verbs_are_left_to_the_classifier(self, verb):
        # A verb is meaning, not a name — and it arrives in whatever language
        # the user speaks, which is precisely why a word list cannot own it.
        assert "relay" not in _augment_protocols_from_keywords(verb, ()), verb

    @pytest.mark.parametrize("chain", [
        "ethereum", "arbitrum", "optimism", "polygon", "avalanche",
        "linea", "scroll", "zksync", "celo", "fantom",
        "base", "bsc", "evm",
    ])
    def test_chain_names_are_left_to_the_classifier(self, chain):
        # "base" is also an ordinary English noun, so matching it here misfires
        # on "database" / "baseline". The classifier reads it in context.
        msg = f"swap 1 SOL to USDC on {chain}"
        assert "relay" not in _augment_protocols_from_keywords(msg, ()), chain


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
        ("debridge swap", "debridge"),
    ])
    def test_known_keyword(self, msg, proto):
        assert proto in _augment_protocols_from_keywords(msg, ()), msg

    def test_squid_is_withheld(self):
        # Squid is integrated but its integrator ID is not issued, so every call
        # 401s. Naming it must not route there — the keyword net, the tool list
        # and the prompt all withhold it until the credential lands.
        assert "squid" not in _augment_protocols_from_keywords("squid router bridge", ())

    def test_classifier_pick_is_preserved(self):
        # If the classifier already picked something, we never drop it.
        result = _augment_protocols_from_keywords("hello", ("jupiter",))
        assert "jupiter" in result

    def test_multiple_protocols_merged(self):
        result = _augment_protocols_from_keywords(
            "swap on raydium then bridge via relay.link", ()
        )
        assert "raydium" in result
        assert "relay" in result

    def test_bare_relay_is_not_a_name(self):
        # "relay.link" and "relay bridge" are names; bare "relay" is an ordinary
        # English word ("relay the message", "relay race"), so it is withheld
        # for the same reason "base" is. The classifier still sees the sentence.
        result = _augment_protocols_from_keywords(
            "swap on raydium then bridge to base via relay", ()
        )
        assert "raydium" in result
        assert "relay" not in result
