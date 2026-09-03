"""What the assistant says it can do.

`capabilities()` is what answers "what can you do" and "what can I do on this
chain". It is assembled from the same registries that gate what the model may
call, which is the point — but a protocol that is registered and reachable can
still be described wrongly, and a wrong description is worse than a missing
one: it sends someone to another chain for something that is live under them.

These tests pin the descriptions that were wrong. Robinhood Chain was grouped
with the chains we only read, so the assistant told users lending and perps
were Solana-only while both were live on the chain they were standing on.
"""

from __future__ import annotations

from app.clients.market_data import (
    _CAPABILITY_GROUPS,
    _PROTOCOL_LABELS,
    _ROBINHOOD_PROTOCOLS,
    capabilities,
)
from app.services.tool_selector import ACTION_TAGS, PROTOCOL_TO_TAGS


def _network(caps: dict, name: str) -> dict | None:
    return next((n for n in caps["networks"] if n["network"] == name), None)


async def test_robinhood_is_its_own_execution_network():
    caps = await capabilities()
    rh = _network(caps, "Robinhood Chain")
    assert rh is not None, "Robinhood Chain has no entry — it is not 'another chain'"
    # The protocols people actually trade through there.
    assert {"Morpho", "Lighter", "SushiSwap", "OpenSea"} <= set(rh["protocols"])


async def test_no_network_note_calls_robinhood_read_only():
    """The exact claim that reached users: 'lending, staking and liquidity
    integrations are Solana-only'. It was true before Morpho and Lighter and
    false afterwards, and the model repeated it verbatim."""
    caps = await capabilities()
    rh = _network(caps, "Robinhood Chain")
    assert "read-only" in rh["note"] or "not a read-only" in rh["note"]

    other = _network(caps, "Other chains")
    if other:
        # It may still say the OTHER EVM chains are read-only — that is true —
        # but it must not leave Robinhood inside that claim.
        assert "Robinhood" in other["note"], (
            "the read-only claim doesn't exclude Robinhood Chain"
        )


async def test_robinhood_protocols_are_not_listed_under_solana():
    caps = await capabilities()
    solana = _network(caps, "Solana")
    assert "Morpho" not in solana["protocols"]
    assert "Lighter" not in solana["protocols"]


async def test_every_protocol_has_a_human_name():
    """Without a label a protocol appears under its raw registry id — 'pons',
    'poolstrade' — which reads as an internal key, not a product."""
    for proto in PROTOCOL_TO_TAGS:
        assert proto in _PROTOCOL_LABELS, f"{proto} would be shown by its id"


async def test_robinhood_set_matches_the_registry():
    """A protocol added to the registry but not to this set would silently be
    described as Solana."""
    assert _ROBINHOOD_PROTOCOLS <= set(PROTOCOL_TO_TAGS), (
        "the Robinhood set names a protocol the registry doesn't have"
    )


async def test_perps_are_a_capability_the_assistant_admits_to():
    """There was no perps area at all, so 'what can you do' never mentioned
    perps on either chain."""
    caps = await capabilities()
    perps = next((g for g in caps["capabilityAreas"] if g["area"] == "Perpetuals"), None)
    assert perps is not None and perps["canDo"] > 0
    assert any(e.startswith("lighter_") for e in perps["examples"]) or perps["canDo"] > 4


async def test_both_robinhood_launchpads_count_as_token_launch():
    caps = await capabilities()
    launch = next(g for g in caps["capabilityAreas"] if g["area"] == "Token launch")
    assert {"pons_launch", "pools_launch"} <= set(launch["examples"])


def test_token_launch_does_not_count_buying_a_launchpad_token():
    """`launchpad` also marks pons_buy / pools_buy. Counting those would call
    trading a launch."""
    want = frozenset(_CAPABILITY_GROUPS["Token launch"])
    assert "launchpad" not in want
    counted = {a for a, t in ACTION_TAGS.items() if t & want}
    assert not any(a.endswith(("_buy", "_sell")) for a in counted)
