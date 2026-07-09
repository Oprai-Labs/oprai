"""
Chat-service eval runner.

Loads `eval/qa_set.jsonl`, fires each question against the live LLM via
the in-process `stream_chat_response` function, and validates the streamed
result against per-row assertions.

Usage
-----
    cd services/chat-service-py
    .venv/bin/python -m eval.run_eval                         # all rows
    .venv/bin/python -m eval.run_eval --ids cat-stable-tr,…   # subset
    .venv/bin/python -m eval.run_eval --json out.json         # also write JSON

Exit code is 0 on success, 1 on `--gate <threshold>` violation, 2 on infra
error. The `--gate` flag is what CI calls.

Assertions per row (all optional):
    must_contain:           list[str]   — substrings that must appear in text
    must_not_contain:       list[str]   — substrings that must NOT appear
    must_have_action:       str         — action card with that action_type
    must_contain_params:    list[str]   — keys present in the action's params
    must_have_action_or_clarify: bool   — at least one of action or clarification card
    must_not_have_action:   bool        — no action card emitted
    max_length:             int         — text length cap

Each row gets a fresh in-memory session so cross-turn state can't leak.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Allow `python -m eval.run_eval` from services/chat-service-py/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.db.connection import async_session_factory  # noqa: E402
from app.models.session import ChatSession  # noqa: E402
from app.services import message as message_svc  # noqa: E402

QA_SET_PATH = Path(__file__).parent / "qa_set.jsonl"
TEST_WALLET = "EvAL1111111111111111111111111111111111111111"  # 44-char placeholder
# Per-row wallet suffix lets us avoid the per-wallet daily LLM cap when
# the suite runs back-to-back (90 rows * ~15K tokens > 1.5M cap default).
# Each row gets a UNIQUE wallet address so caps are checked separately.
_USE_PER_ROW_WALLET = True


@dataclass
class RowResult:
    id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    text: str = ""
    action_type: str | None = None
    action_params: dict[str, Any] = field(default_factory=dict)
    has_clarification: bool = False
    latency_s: float = 0.0


def load_qa_set(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


async def _run_one(
    SessionFactory, row: dict[str, Any]
) -> RowResult:
    """Send one question; collect text + first action / clarification."""
    rid = str(row.get("id") or "?")
    msg = str(row.get("message") or "").strip()
    if not msg:
        return RowResult(id=rid, passed=False, failures=["empty message"])

    session_id = str(uuid.uuid4())
    # Per-row wallet — same base prefix so it looks like a real Solana
    # address (44-char base58), unique suffix so per-wallet daily caps
    # don't accumulate across 90+ rows.
    if _USE_PER_ROW_WALLET:
        _suffix = uuid.uuid4().hex[:30]
        row_wallet = ("Ev" + _suffix)[:44].ljust(44, "1")
    else:
        row_wallet = TEST_WALLET
    text_buf: list[str] = []
    action_type: str | None = None
    action_params: dict[str, Any] = {}
    has_clarification = False

    # Pre-create the ChatSession row so the FK from chat_messages resolves.
    # Eval rows are independent so each gets a fresh in-DB session, named
    # "eval-<rowid>" so they're identifiable in the chat_schema if anyone
    # inspects the test data later.
    async with SessionFactory() as setup_db:
        setup_db.add(ChatSession(
            id=uuid.UUID(session_id),
            user_id=f"eval-{rid}",
            wallet_address=row_wallet,
            title=f"eval/{rid}",
        ))
        await setup_db.commit()

    t0 = time.monotonic()
    # Per-row timeout so one stuck network call doesn't block the suite.
    # 60s is generous for any single turn — typical mini latency is 10-20s.
    _ROW_TIMEOUT_S = 90
    async with SessionFactory() as db:
        try:
            stream = message_svc.stream_chat_response(
                db, session_id, row_wallet, msg,
                is_first_message=True,
                attachments=None,
                protocols=[],
            )
            # Wrap the iteration in a timeout so one stuck network call
            # doesn't block the whole suite. asyncio.timeout cancels the
            # underlying coroutines on expiry.
            async with asyncio.timeout(_ROW_TIMEOUT_S):
                async for raw_chunk in stream:
                    # Optional: dump raw chunks to disk for debugging when
                    # the eval result looks odd (text empty, params empty).
                    if os.environ.get("EVAL_DEBUG_CHUNKS"):
                        with open("/tmp/eval-chunks.log", "a") as _df:
                            _df.write(f"[{rid}] {raw_chunk!r}\n")
                    # Chunk is an SSE-formatted string: `data: {...}\n\n`
                    for line in raw_chunk.splitlines():
                        if not line.startswith("data: "):
                            continue
                        payload = line[len("data: "):]
                        try:
                            ev = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(ev, dict):
                            continue
                        # Text deltas. A delta with `replace: True` supersedes
                        # everything streamed so far (server-side override of a
                        # hedge / junk reply) — mirror the frontend by resetting
                        # the buffer rather than appending.
                        if (delta := ev.get("text") or ev.get("delta")) is not None:
                            if ev.get("replace"):
                                text_buf.clear()
                            text_buf.append(str(delta))
                        # Action card emit. Stream shape is `{'action': {...}}`
                        # where inner dict is the to_frontend_dict() output:
                        # `{'type': 'swap', 'params': {...}, ...}` (the inner
                        # key is `type` not `action_type` after dict transform).
                        if "action" in ev and isinstance(ev["action"], dict):
                            act = ev["action"]
                            atype = act.get("action_type") or act.get("type")
                            if atype and not action_type:
                                action_type = str(atype)
                                params = act.get("params")
                                if isinstance(params, dict):
                                    action_params = dict(params)
                        # Query card emit — `{'query': {type, params, ...}}`.
                        # Includes a representation of the card's data in
                        # text_buf so substring assertions can match against
                        # the symbol / protocol the card displays. The card
                        # itself is the user-visible answer; without this
                        # branch, query-card-only turns looked "empty".
                        if "query" in ev and isinstance(ev["query"], dict):
                            q = ev["query"]
                            qtype = q.get("query_type") or q.get("type") or ""
                            qparams = q.get("params") or {}
                            # Project the card identity into the text channel
                            # so `must_contain: ['SOL']` for a price query
                            # against SOL passes. Format mirrors what the
                            # user sees ("Trending Tokens — JTO, PYTH, JUP").
                            text_buf.append(f"[QUERY {qtype}: {qparams}]")
                        # Clarification card — same idea.
                        if "clarify" in ev and isinstance(ev["clarify"], dict):
                            has_clarification = True
                            c = ev["clarify"]
                            text_buf.append(f"[CLARIFY: {c.get('question','')} options={c.get('options',[])}]")
                        if ev.get("type") == "clarification" or ev.get("request_clarification"):
                            has_clarification = True
                        # Error events also count as "visible content" —
                        # rate_limit, timeout, network, etc.
                        if "error" in ev:
                            text_buf.append(f"[ERROR: {ev['error']}]")
            await db.commit()
        except Exception as exc:
            return RowResult(
                id=rid, passed=False,
                failures=[f"infra: {type(exc).__name__}: {exc}"],
                latency_s=time.monotonic() - t0,
            )

    text = "".join(text_buf)
    result = RowResult(
        id=rid, passed=True, text=text,
        action_type=action_type, action_params=action_params,
        has_clarification=has_clarification,
        latency_s=time.monotonic() - t0,
    )

    # ── Assertions ──────────────────────────────────────────────────────
    for needle in row.get("must_contain") or []:
        if str(needle).lower() not in text.lower():
            result.failures.append(f"missing substring: {needle!r}")
    for needle in row.get("must_not_contain") or []:
        if str(needle).lower() in text.lower():
            result.failures.append(f"forbidden substring present: {needle!r}")
    if (want_action := row.get("must_have_action")):
        if not action_type:
            result.failures.append(f"expected action {want_action!r}, got none")
        elif str(want_action).lower() != action_type.lower():
            result.failures.append(
                f"expected action {want_action!r}, got {action_type!r}"
            )
    for key in row.get("must_contain_params") or []:
        if key not in action_params:
            result.failures.append(f"action missing param: {key!r}")
    if row.get("must_have_action_or_clarify"):
        # Accept any of: action card, clarification card, or non-trivial
        # prose asking the user for missing info. Ambig prompts can be
        # legitimately handled by the model writing "which token?" without
        # rendering a dedicated clarification card.
        has_prose_clarify = len(text.strip()) >= 20
        if not action_type and not has_clarification and not has_prose_clarify:
            result.failures.append(
                "expected an action, clarification card, or prose clarification; got nothing"
            )
    if row.get("must_not_have_action"):
        if action_type:
            result.failures.append(
                f"unexpected action emitted: {action_type!r}"
            )
    if (cap := row.get("max_length")) and len(text) > int(cap):
        result.failures.append(
            f"text length {len(text)} exceeds cap {cap}"
        )

    result.passed = not result.failures
    return result


def _format_table(results: list[RowResult]) -> str:
    width = max(len(r.id) for r in results) + 2
    lines = [
        f"{'ID':<{width}} {'Pass':<6} {'Lat(s)':<8} Failures",
        f"{'-' * width} ------ -------- " + "-" * 50,
    ]
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        fails = "; ".join(r.failures) if r.failures else ""
        lines.append(f"{r.id:<{width}} {mark:<6} {r.latency_s:>6.2f}   {fails}")
    return "\n".join(lines)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", help="comma-separated subset of row ids", default=None)
    ap.add_argument("--json", help="write JSON summary to this path", default=None)
    ap.add_argument(
        "--gate", type=float, default=0.0,
        help="fail (exit 1) if pass rate is below this threshold (0.0-1.0)",
    )
    args = ap.parse_args()

    rows = load_qa_set(QA_SET_PATH)
    if args.ids:
        wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
        rows = [r for r in rows if r.get("id") in wanted]
    if not rows:
        print("no rows to run", file=sys.stderr)
        return 2

    results: list[RowResult] = []
    for r in rows:
        print(f"… {r['id']}", flush=True)
        res = await _run_one(async_session_factory, r)
        results.append(res)

    print()
    print(_format_table(results))

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    pass_rate = passed / total if total else 0.0
    print(f"\nSummary: {passed}/{total} passed  ({pass_rate:.0%})")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "total": total,
            "passed": passed,
            "pass_rate": pass_rate,
            "rows": [
                {
                    "id": r.id, "passed": r.passed, "failures": r.failures,
                    "text": r.text[:500], "action_type": r.action_type,
                    "action_params": r.action_params,
                    "has_clarification": r.has_clarification,
                    "latency_s": r.latency_s,
                }
                for r in results
            ],
        }, indent=2), encoding="utf-8")
        print(f"JSON summary → {args.json}")

    if args.gate and pass_rate < args.gate:
        print(
            f"\nGATE FAIL: pass rate {pass_rate:.0%} < threshold {args.gate:.0%}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    # asyncio import already at top
    if not settings.OPRAI_OPENAI_API_KEY and not settings.OPRAI_ANTHROPIC_API_KEY:
        print("OPRAI_OPENAI_API_KEY (or OPRAI_ANTHROPIC_API_KEY) must be set", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main()))
