"""The capability list must come from the registries, not from prose.

A hand-maintained feature list drifts from the product within a release or
two, and then tells a user about something that no longer works — or, worse,
stays silent about something that does.
"""
import asyncio

from app.clients.market_data import (
    _DISPATCH,
    _NOT_OFFERED,
    _describe_integrated_chains,
    capabilities,
)
from app.services.tool_selector import ACTION_TAGS, PROTOCOL_TO_TAGS, QUERY_TAGS


def _caps():
    return asyncio.run(capabilities())


def test_it_is_reachable_like_any_other_query():
    assert "capabilities" in _DISPATCH


def test_nothing_switched_off_is_advertised():
    """Being in the registry is not the same as working.

    Tensor, Streamflow, deBridge and Save all dispatch and validate when
    called — from the code there is no way to tell they are not live. Listing
    them promised users features that do not exist.
    """
    caps = _caps()
    listed = {p["id"] for p in caps["protocolDetail"]}
    for off in _NOT_OFFERED:
        assert off not in listed, f"{off} is not offered but is being advertised"
    for network in caps["networks"]:
        for name in network["protocols"]:
            assert name.lower() not in {"tensor", "streamflow", "debridge"}


def test_a_capability_with_no_live_venue_disappears():
    # Payment streaming existed only through Streamflow. With it switched off
    # the whole area must go, not sit there with a count behind nothing.
    caps = _caps()
    areas = {g["area"] for g in caps["capabilityAreas"]}
    assert "Payments and streaming" not in areas


def test_the_bridged_networks_are_not_described_as_defi_venues():
    # Lending, staking and liquidity are Solana-only. Someone told otherwise
    # goes looking for a lending market on Base.
    caps = _caps()
    bridged = next(n for n in caps["networks"] if n["network"] != "Solana")
    assert "Solana-only" in bridged["note"]
    assert set(bridged["protocols"]) <= {"Relay"}


def test_no_chain_is_named_when_the_list_cannot_be_read():
    # The gateway is not reachable from a test run. The answer must say the
    # list is unavailable rather than fall back to a remembered one.
    caps = _caps()
    bridged = next(n for n in caps["networks"] if n["network"] != "Solana")
    assert bridged.get("chainsUnavailable") is True
    assert "chains" not in bridged


# What the gateway answers, built from the readers themselves.
_GATEWAY_SAMPLE = {
    "chains": [
        {"chain": "ethereum", "label": "Ethereum", "nativeSymbol": "ETH",
         "reads": ["balances", "nfts", "positions", "transactions"]},
        {"chain": "bsc", "label": "BNB Chain", "nativeSymbol": "BNB",
         "reads": ["balances", "nfts", "positions", "transactions"]},
        {"chain": "robinhood", "label": "Robinhood Chain", "nativeSymbol": "ETH",
         "reads": ["balances", "nfts", "transactions"]},
        {"chain": "ghost", "label": "Ghost", "nativeSymbol": "", "reads": []},
    ],
    "count": 4,
}


def test_only_chains_something_actually_reads_are_named():
    described = _describe_integrated_chains(_GATEWAY_SAMPLE)
    named = [c["chain"] for c in described["chains"]]
    # A chain no reader covers is not a chain we can show anyone anything on.
    assert "Ghost" not in named
    assert named == ["Ethereum", "BNB Chain", "Robinhood Chain"]
    assert described["chainCount"] == 3


def test_coverage_is_carried_per_chain_not_flattened():
    # Robinhood Chain has no DeFi-position provider. One word for every chain
    # would invent one, and someone would go looking for the view.
    described = _describe_integrated_chains(_GATEWAY_SAMPLE)
    rh = next(c for c in described["chains"] if c["chain"] == "Robinhood Chain")
    assert "positions" not in rh["reads"]
    eth = next(c for c in described["chains"] if c["chain"] == "Ethereum")
    assert "positions" in eth["reads"]


def test_an_empty_or_broken_answer_names_nothing():
    assert _describe_integrated_chains({}) is None
    assert _describe_integrated_chains({"chains": []}) is None
    assert _describe_integrated_chains({"chains": ["nonsense"]}) is None


def test_every_wired_protocol_is_named():
    # A protocol in the registry with something reachable must appear. Adding
    # one and forgetting to advertise it is the drift this exists to stop.
    caps = _caps()
    listed = {p["id"] for p in caps["protocolDetail"]}
    for proto, tags in PROTOCOL_TO_TAGS.items():
        reachable = any(t & tags for t in ACTION_TAGS.values()) or any(
            t & tags for t in QUERY_TAGS.values()
        )
        if reachable and proto not in _NOT_OFFERED:
            assert proto in listed, f"{proto} is wired but not advertised"


def test_nothing_is_advertised_that_cannot_be_called():
    caps = _caps()
    for p in caps["protocolDetail"]:
        assert p["actions"] + p["reads"] > 0, f"{p['id']} listed with nothing behind it"


def test_networks_separate_solana_from_the_bridged_chains():
    # The protocol integrations are Solana-only; the other chains are reached
    # by bridging. Blurring that tells someone they can lend on Base.
    caps = _caps()
    by_name = {n["network"]: n for n in caps["networks"]}
    solana = next(n for k, n in by_name.items() if "Solana" in k)
    bridged = next(n for k, n in by_name.items() if "Solana" not in k)
    assert "Kamino" in solana["protocols"]
    assert "Kamino" not in bridged["protocols"]
    assert bridged["protocols"], "the bridged networks must still name how they are reached"


def test_the_totals_match_the_registries():
    caps = _caps()
    assert caps["totals"]["actions"] == len(ACTION_TAGS)
    assert caps["totals"]["reads"] == len(QUERY_TAGS)
