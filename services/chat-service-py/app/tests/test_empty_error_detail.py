"""A failure with no message must still say something.

An httpx ReadTimeout carries an empty string. Passed straight through it
became {"error": ""} — a failure the model could say nothing about, so it
said nothing at all and the user's question vanished off the screen.
"""
import httpx


def _detail(exc: Exception) -> str:
    return str(exc).strip() or type(exc).__name__


def test_a_timeout_still_names_itself():
    assert str(httpx.ReadTimeout("")) == ""
    assert _detail(httpx.ReadTimeout("")) == "ReadTimeout"


def test_a_real_message_survives():
    assert _detail(ValueError("pool not found")) == "pool not found"


def test_whitespace_is_not_a_message():
    assert _detail(ValueError("   ")) == "ValueError"
