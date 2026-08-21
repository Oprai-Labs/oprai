"""Every case here is a fact that was actually written to a live account."""
from app.services.user_facts import _plausible


def test_a_generic_noun_is_not_a_wallet_name():
    # "cüzdan" is the Turkish word for "wallet". The extractor saw the noun in
    # the sentence and stored it as the answer, at confidence 1.0.
    assert not _plausible("preferred_wallet", "cüzdan")
    assert not _plausible("preferred_wallet", "wallet")
    assert _plausible("preferred_wallet", "Phantom")
    assert _plausible("preferred_wallet", "  solflare ")


def test_venues_are_not_holdings():
    # A live account had usually_holds = ["Meteora", "Raydium", "Pump.fun"],
    # which read back as "this user usually holds Meteora".
    assert not _plausible("usually_holds", ["Meteora", "Raydium", "Pump.fun"])
    assert not _plausible("usually_holds", ["SOL", "Kamino"])
    assert _plausible("usually_holds", ["SOL", "USDC", "JITOSOL"])
    assert not _plausible("usually_holds", [])


def test_a_cent_is_not_a_stated_position_limit():
    # 0.01 came from reading a test wallet's balances, not from anything the
    # user said.
    assert not _plausible("max_position_size_usd", 0.01)
    assert not _plausible("max_position_size_usd", "not a number")
    assert _plausible("max_position_size_usd", 500)


def test_an_unknown_venue_is_not_a_dex_preference():
    assert not _plausible("preferred_dex", "borsa")
    assert _plausible("preferred_dex", "Raydium")


def test_types_without_a_rule_are_left_alone():
    # The validator exists to catch known failures, not to police every field.
    assert _plausible("risk_tolerance", "high")
    assert _plausible("language", "Turkish")
