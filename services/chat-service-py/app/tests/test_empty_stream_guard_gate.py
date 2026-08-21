"""The empty-stream guard must key on what the user can see.

A strategy read timed out, the model produced no text, and the question
vanished from the screen — because the guard built to prevent exactly that
stood down on the grounds that a tool call had been *made*. Making a call and
showing something are different things: card types render live results that
speak for themselves; the rest only feed data back to the model.
"""
from app.services.message import QUERY_CARD_RENDER_TYPES


class _Q:
    def __init__(self, value):
        self.type = type("T", (), {"value": value})()


def _rendered(queries):
    return any(
        getattr(q, "type", None) and q.type.value in QUERY_CARD_RENDER_TYPES
        for q in queries
    )


def test_a_strategy_read_puts_nothing_on_screen():
    # token_strategies feeds the model and renders no card, so a failure here
    # leaves the user with a blank bubble unless the guard fires.
    assert "token_strategies" not in QUERY_CARD_RENDER_TYPES
    assert not _rendered([_Q("token_strategies")])


def test_a_card_type_speaks_for_itself():
    card = next(iter(QUERY_CARD_RENDER_TYPES))
    assert _rendered([_Q(card)])


def test_no_queries_at_all_is_still_nothing_rendered():
    assert not _rendered([])
