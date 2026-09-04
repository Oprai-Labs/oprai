"""Naming a chain must select the chain, not one protocol on it.

"robinhood" is matched as a Relay keyword so that naming an EVM chain never
drops a request into the Solana flow. The side effect was that our own chain's
venues went out of scope the moment someone said its name: "best lending rates
on Robinhood Chain" resolved to the bridge and was answered from the Solana
lending aggregator, which quotes nothing for it — the user was told no rates
were available while four Morpho markets were live.
"""

from __future__ import annotations

import pytest

from app.services.intent_router import _augment_protocols_from_keywords as protocols


def sel(msg: str) -> set[str]:
    return set(protocols(msg, ()))


@pytest.mark.parametrize(
    "msg, venue",
    [
        ("What are the best lending rates on Robinhood Chain?", "morpho"),
        ("I want to borrow USDG on Robinhood", "morpho"),
        ("repay my loan on robinhood chain", "morpho"),
        ("open a long on NVDA on Robinhood chain", "lighter"),
        ("what leverage can I get on robinhood", "lighter"),
        ("swap USDG to USDe on robinhood", "sushi"),
        ("what nfts are trending on robinhood", "opensea"),
    ],
)
def test_a_verb_plus_a_chain_name_brings_that_chains_venue_into_scope(msg, venue):
    assert venue in sel(msg), f"{venue} was not offered for: {msg}"


def test_the_bridge_still_wins_a_bridging_ask():
    """The rule that put chain names on Relay exists so a cross-chain ask never
    falls into the Solana flow. It must keep doing that."""
    assert sel("bridge 0.1 ETH from base to robinhood") == {"relay"}


def test_the_wrong_chains_venue_is_dropped():
    """Offering Kamino for a Robinhood lending question is how the answer ends
    up being about the wrong chain."""
    assert "kamino" not in sel("lending rates on robinhood chain")
    assert "magic_eden" not in sel("nft floor price on robinhood")


def test_a_protocol_the_user_named_is_never_dropped():
    """Asking about Kamino while mentioning an EVM chain is still a question
    about Kamino — the correction must not overrule what someone typed."""
    assert "kamino" in sel("kamino lending on base")


def test_solana_questions_are_left_alone():
    """The rule is keyed on an EVM chain being named; without one it must not
    fire, or every lending question would drag Morpho along."""
    assert sel("best lending rates on Solana") == set()
    assert "morpho" not in sel("what can I earn lending SOL")
