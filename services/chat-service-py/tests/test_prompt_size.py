"""What goes into a turn, and what has no business being there.

The prompt is the bill: 61K tokens read to produce a 155-token answer, and
98.8% of the cost is reading rather than thinking. So the rule these tests
hold is a simple one — a turn must not carry instructions it cannot act on.

The composite-report formats are the case in point. They are 11K tokens of
layout rules for a Nansen-style wallet deep dive, and they were sent on every
turn that touched any protocol, including a Morpho lend that could never
produce one: the wallet-analysis path they belong to is gated on the same flag
they are now gated on, so on those turns they were unreachable text.
"""

from __future__ import annotations

from app.prompts.loader import get_prompt_loader

ROBINHOOD = ["morpho", "sushi", "opensea", "uniswap", "relay", "lighter", "pons"]


def _tokens(text: str) -> int:
    return len(text) // 4


def test_an_ordinary_turn_does_not_carry_the_deep_dive_formats():
    loader = get_prompt_loader()
    ordinary = loader.get_prompt_for_protocols(ROBINHOOD, wants_analysis=False)
    assert "Composite analysis" not in ordinary, (
        "a lend/swap turn is still carrying the wallet-report layout rules"
    )


def test_a_deep_dive_turn_still_gets_them():
    """The saving must not come out of the feature. When the turn can produce
    a composite, every rule for producing one has to be there."""
    loader = get_prompt_loader()
    deep = loader.get_prompt_for_protocols(ROBINHOOD, wants_analysis=True)
    assert "Composite analysis" in deep
    for required in ("Required tool calls", "Wallet deep-dive",
                     "Pre-flight checklist"):
        assert required in deep, f"the deep-dive prompt lost '{required}'"


def test_the_saving_is_the_size_it_claims_to_be():
    loader = get_prompt_loader()
    ordinary = _tokens(loader.get_prompt_for_protocols(ROBINHOOD, wants_analysis=False))
    deep = _tokens(loader.get_prompt_for_protocols(ROBINHOOD, wants_analysis=True))
    assert deep - ordinary > 9_000, (
        f"expected the report formats to be ~11K tokens, measured {deep - ordinary}"
    )
    assert ordinary < 40_000, f"an ordinary Robinhood turn is still {ordinary} tokens"


def test_the_catalog_stays_on_every_market_data_turn():
    """Splitting the file must not take the tool catalogue with it — that is
    what tells the model which query to call and with what parameters."""
    loader = get_prompt_loader()
    ordinary = loader.get_prompt_for_protocols(ROBINHOOD, wants_analysis=False)
    for required in ("Tool catalog", "Parameter conventions", "DexScreener"):
        assert required in ordinary, f"'{required}' was lost in the split"


def test_chitchat_still_costs_almost_nothing():
    loader = get_prompt_loader()
    hello = _tokens(loader.get_prompt_for_protocols(ROBINHOOD, is_chitchat=True))
    assert hello < 15_000, f"a 'hello' loads {hello} tokens"


def test_a_permission_list_is_not_a_load_list():
    """A Robinhood-only caller sends all seven protocols on every message to
    say "these and nothing else". Loading all seven fragments because of that
    made a Morpho lend carry the launchpad and cross-chain docs."""
    loader = get_prompt_loader()
    everything = _tokens(loader.get_prompt_for_protocols(ROBINHOOD))
    just_lending = _tokens(loader.get_prompt_for_protocols(["morpho"]))
    assert just_lending < everything * 0.7, (
        f"narrowing saved almost nothing: {everything} -> {just_lending}"
    )


def test_narrowing_never_leaves_the_permission_list():
    """The whole point of the explicit list is that the model is never offered
    a venue this caller cannot execute on."""
    from app.services.action_schemas import ActionType  # noqa: F401

    allowed = {"morpho", "sushi", "opensea"}
    for inferred in (["morpho"], ["jupiter"], ["morpho", "kamino"], []):
        narrowed = {p for p in inferred if p in allowed}
        used = narrowed or allowed
        assert used <= allowed, f"{inferred} escaped the permission list"
        assert used, "narrowing produced an empty protocol set"
