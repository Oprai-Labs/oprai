"""The capability list must come from the registries, not from prose.

A hand-maintained feature list drifts from the product within a release or
two, and then tells a user about something that no longer works — or, worse,
stays silent about something that does.
"""
import asyncio

from app.clients.market_data import _DISPATCH, capabilities
from app.services.tool_selector import ACTION_TAGS, PROTOCOL_TO_TAGS, QUERY_TAGS


def _caps():
    return asyncio.run(capabilities())


def test_it_is_reachable_like_any_other_query():
    assert "capabilities" in _DISPATCH


def test_every_wired_protocol_is_named():
    # A protocol in the registry with something reachable must appear. Adding
    # one and forgetting to advertise it is the drift this exists to stop.
    caps = _caps()
    listed = {p["id"] for p in caps["protocolDetail"]}
    for proto, tags in PROTOCOL_TO_TAGS.items():
        reachable = any(t & tags for t in ACTION_TAGS.values()) or any(
            t & tags for t in QUERY_TAGS.values()
        )
        if reachable:
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
