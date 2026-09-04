"""An answer that was produced must not be lost to a dropped connection.

The failure this guards against actually happened: the model had streamed a
complete report, and the pooled database connection went away underneath the
session while it was being saved. The commit raised, the person saw an error,
and the only copy of the work was in a log line. Everything that cost anything
had already been paid for by then.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import InterfaceError, IntegrityError, PendingRollbackError

from app.services.message import _commit_answer


class _Msg:
    def __init__(self):
        self.id = uuid.uuid4()
        self.session_id = uuid.uuid4()
        self.wallet_address = "0xabc"
        self.content = "## DELTA — overview\n\nRisk 23/100."
        self.metadata_ = None


class _Session:
    def __init__(self, fail_with=None):
        self.fail_with = fail_with
        self.commits = 0
        self.rolled_back = False

    async def commit(self):
        self.commits += 1
        if self.fail_with is not None:
            raise self.fail_with

    async def rollback(self):
        self.rolled_back = True


@pytest.mark.asyncio
async def test_a_healthy_commit_is_left_alone():
    db = _Session()
    await _commit_answer(db, _Msg(), "sess", "0xabc")
    assert db.commits == 1
    assert not db.rolled_back, "a successful commit was rolled back"


@pytest.mark.asyncio
async def test_a_dropped_connection_is_retried_on_a_fresh_session(monkeypatch):
    """The broken session cannot be reused — it has to be rolled back and the
    row written through a new one."""
    saved = []

    class _Fresh:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def add(self, row): saved.append(row)
        async def commit(self): pass

    import app.db.connection as conn
    monkeypatch.setattr(conn, "async_session_factory", lambda: _Fresh())

    msg = _Msg()
    db = _Session(fail_with=PendingRollbackError("connection is closed"))
    await _commit_answer(db, msg, "sess", "0xabc")

    assert db.rolled_back, "the broken session was reused without a rollback"
    assert len(saved) == 1, "the answer was not re-saved"
    assert saved[0].content == msg.content
    assert saved[0].id == msg.id, "the retry wrote a different row"


@pytest.mark.asyncio
async def test_losing_the_answer_is_logged_loudly_not_swallowed(monkeypatch, caplog):
    """When even the retry fails there is nothing left to try — but a lost
    answer must never be a silent event."""
    class _AlsoBroken:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def add(self, row): pass
        async def commit(self): raise InterfaceError("still down", None, None)

    import app.db.connection as conn
    monkeypatch.setattr(conn, "async_session_factory", lambda: _AlsoBroken())

    db = _Session(fail_with=InterfaceError("connection is closed", None, None))
    with caplog.at_level("ERROR"):
        await _commit_answer(db, _Msg(), "sess-42", "0xabc")
    assert any("answer_lost" in r.message for r in caplog.records), (
        "an answer was lost without an error-level log"
    )


@pytest.mark.asyncio
async def test_the_stream_survives_a_lost_answer(monkeypatch):
    """Saving is not the reply. A failure to persist must not raise into the
    stream and turn a delivered answer into an error message."""
    class _AlsoBroken:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def add(self, row): pass
        async def commit(self): raise InterfaceError("down", None, None)

    import app.db.connection as conn
    monkeypatch.setattr(conn, "async_session_factory", lambda: _AlsoBroken())

    db = _Session(fail_with=InterfaceError("down", None, None))
    await _commit_answer(db, _Msg(), "sess", "0xabc")  # must not raise
