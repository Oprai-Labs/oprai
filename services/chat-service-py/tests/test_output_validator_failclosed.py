"""The semantic output-validator is a backstop that fails OPEN on outages, but a
REACHABLE-yet-unusable verdict (blank / non-JSON / wrong shape) must fail CLOSED
for value-moving actions so a garbled validator response can't wave a fund action
through. Reads still fail open."""
from app.services.output_validator import _unavailable_verdict


def test_fund_moving_action_fails_closed():
    v = _unavailable_verdict("execute_action")
    assert v.ok is False
    assert v.severity == "block"


def test_reads_still_fail_open():
    assert _unavailable_verdict("query_onchain").ok is True
    assert _unavailable_verdict("some_other_tool").ok is True
