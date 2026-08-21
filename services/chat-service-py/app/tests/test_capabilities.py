"""The capability list must come from the registries, not from prose.

A hand-maintained feature list drifts from the product within a release or
two, and then tells a user about something that no longer works — or, worse,
stays silent about something that does.
"""
import asyncio

from app.clients.market_data import (
    _DISPATCH,
    _NOT_OFFERED,
    _group_bridged_chains,
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


def test_no_chain_is_named_when_the_live_list_cannot_be_read():
    # The bridge is not reachable from a test run. The answer must say the
    # list is unavailable rather than fall back to a remembered one — a stale
    # chain list is how eighteen chains stood in for sixty-five.
    caps = _caps()
    bridged = next(n for n in caps["networks"] if n["network"] != "Solana")
    assert bridged.get("chainsUnavailable") is True
    assert "chainsByFamily" not in bridged


# A trimmed copy of what Relay actually answers, including the three shapes
# that have to be handled: a chain that is off, one that is live but cannot be
# deposited to, and Solana itself.
_RELAY_SAMPLE = [
    {"id": 1, "displayName": "Ethereum", "vmType": "evm", "depositEnabled": True},
    {"id": 8453, "displayName": "Base", "vmType": "evm", "depositEnabled": True},
    {"id": 4663, "displayName": "Robinhood Chain", "vmType": "evm", "depositEnabled": True},
    {"id": 42170, "displayName": "Arbitrum Nova", "vmType": "evm", "depositEnabled": False},
    {"id": 999, "displayName": "Off Chain", "vmType": "evm", "disabled": True},
    {"id": 8253038, "displayName": "Bitcoin", "vmType": "bvm", "depositEnabled": True},
    {"id": 728126428, "displayName": "Tron", "vmType": "tvm", "depositEnabled": True},
    {"id": 792703809, "displayName": "Solana", "vmType": "svm", "depositEnabled": True},
    {"id": 9286185, "displayName": "Eclipse", "vmType": "svm", "depositEnabled": True},
]


def test_chains_are_grouped_by_what_they_actually_run():
    grouped = _group_bridged_chains(_RELAY_SAMPLE)
    fam = grouped["chainsByFamily"]
    # Robinhood Chain runs the EVM. It was given a heading of its own, which
    # read as though it were a separate kind of network.
    assert "Robinhood Chain" in fam["EVM"]
    assert not any(f == "Robinhood Chain" for f in fam)
    # And not everything reachable is EVM — calling the whole set "EVM chains"
    # was the other half of the same mistake.
    assert fam["Bitcoin"] == ["Bitcoin"]
    assert fam["Tron"] == ["Tron"]


def test_unusable_chains_are_left_out_and_solana_is_not_a_destination():
    grouped = _group_bridged_chains(_RELAY_SAMPLE)
    named = [c for chains in grouped["chainsByFamily"].values() for c in chains]
    assert "Arbitrum Nova" not in named  # live, but cannot be deposited to
    assert "Off Chain" not in named
    assert "Solana" not in named  # it is a network here, not somewhere to bridge to
    assert "Eclipse" in named  # also SVM, and genuinely a destination
    assert grouped["chainCount"] == len(named) == 6


def test_the_biggest_family_is_listed_first():
    # Sixty-odd names in arbitrary order reads as a dump; leading with the
    # family that holds most of them is what makes the reach legible.
    grouped = _group_bridged_chains(_RELAY_SAMPLE)
    assert next(iter(grouped["chainsByFamily"])) == "EVM"


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
