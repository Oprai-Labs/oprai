"""What the person sees when the connection, not the question, is the problem.

A redeploy or a reset keep-alive used to surface as "⚠️ couldn't reach OPRAI:"
— a failure notice for a question that was fine, and one that stopped at the
colon because some httpx errors stringify to nothing.
"""

from __future__ import annotations

import httpx
import pytest

from app.services import chat




# ── a dropped connection ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_dropped_connection_is_retried_once(monkeypatch):
    """Restarting the bot, or any reset connection, showed the person
    "couldn't reach OPRAI" for a question that was perfectly fine. One retry
    costs half a second and turns the blip back into an answer."""
    calls = {"n": 0}

    async def flaky(jwt, session_id, content, on_progress, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise chat._Transport(httpx.ConnectError(""))
        return chat.Answer(session_id="s", raw="here you go")

    monkeypatch.setattr(chat, "_ask", flaky)
    answer = await chat.stream("jwt", "s", "hello")
    assert answer.text == "here you go"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_a_half_streamed_answer_is_not_asked_again(monkeypatch):
    """If text had already arrived the model has already run. Asking again
    runs it twice and charges twice for one question."""
    calls = {"n": 0}

    async def dies_mid_answer(jwt, session_id, content, on_progress, timeout):
        calls["n"] += 1
        raise chat._Transport(httpx.ReadError("reset"), streamed=True)

    monkeypatch.setattr(chat, "_ask", dies_mid_answer)
    with pytest.raises(chat.ChatError):
        await chat.stream("jwt", "s", "hello")
    assert calls["n"] == 1, "a partly-answered question was re-run"


def test_an_unreachable_service_never_reports_an_empty_reason():
    """The screen read "⚠️ couldn't reach OPRAI:" — a sentence that stops at
    the colon, because some httpx errors stringify to nothing."""
    for e in (httpx.ConnectError(""), httpx.ReadError(""), httpx.HTTPError("")):
        text = chat._transport_message(e)
        assert not text.rstrip().endswith(":"), text
        assert text.strip() and len(text.split()) > 3, text
