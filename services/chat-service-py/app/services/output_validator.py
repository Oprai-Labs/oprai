"""Second-pass LLM sanity check on tool calls before they reach the user.

The responder model emits a tool call (`execute_action` / `query_onchain` /
`request_clarification`). Each call has already been parsed against a
Pydantic schema and address-format-validated. This module adds a third
gate: a small fast LLM examines the call against the user's actual message
and returns a verdict — does this action *make sense* for what the user
asked?

Why: schema validation catches malformed JSON, address regex catches
gibberish, but neither catches "user asked to swap 0.1 SOL → assistant
emits transfer_token to=Hw...XtK6 amount=5". That kind of semantic drift
is what the validator catches.

Cost is negligible (~$0.0001 per call with gpt-4o-mini, 200 input tokens
and 50 output tokens), and the latency adds <300ms which lands during the
streaming text generation anyway.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


_CLIENT: Any = None


_VALIDATOR_SYSTEM = """You audit tool calls produced by a Solana DeFi assistant.
You receive (1) the user's most recent message, (2) the tool call the
assistant intends to make. Decide whether the call is a reasonable response
to that message, then return a JSON verdict.

IMPORTANT — multi-language awareness:
The user message may be in any language. Judge intent by grammatical mood and
context, not by language. A short message that pairs an asset name and/or
amount with an action verb in imperative form is a command to execute — not a
question about rates or mechanics. Do NOT classify imperative commands as
question mismatches just because they are not in English.

Look for:
- Action vs question mismatch — only flag when the user is ASKING (e.g., "what
  is the SOL price?", "show me the APY") but the assistant calls execute_action.
  Do NOT flag imperative commands in any language as mismatches.
- Token / amount mismatch ("swap 0.1 SOL" but params show amount=5 or
  inputMint=USDC).
- Wrong protocol routing (user said "kamino" but action_type=marginfi_*).
- Address-shaped fabrication (params contain a base58 mint but the user
  never mentioned that token by symbol or address; cross-reference the
  user message text).
- Unsafe parameter values (transferring to "11111111111111111111111111111111"
  is the system program, never a user wallet).

Do NOT flag:
- Minor stylistic choices (which staking provider when user said "stake").
- Reasonable defaults (default slippage, default fee tier).
- Cases where the assistant is asking for clarification — those are fine.
- Imperative / command phrasings in any language as "question mismatch".

Output JSON exactly:
{
  "ok": true | false,
  "reason": "<short — only when ok=false>",
  "severity": "block" | "warn"   // only when ok=false; "block" stops the call
}

Severity guidance:
- "block": fabricated address, wrong token, dangerous wallet (system
  program, all-zeros, etc.), clear action-vs-question mismatch.
- "warn":  ambiguous routing, suboptimal but legal parameters.

When ok=true return {"ok": true} only — no extra fields."""


class ValidatorVerdict:
    __slots__ = ("ok", "reason", "severity")

    def __init__(self, ok: bool, reason: str = "", severity: str = "") -> None:
        self.ok = ok
        self.reason = reason
        self.severity = severity

    @property
    def should_block(self) -> bool:
        return (not self.ok) and self.severity == "block"


async def validate_tool_call(
    user_message: str,
    tool_name: str,
    tool_args: dict | str,
) -> ValidatorVerdict:
    """Run the second-pass check.

    Failure modes (network error, invalid JSON, missing API key) return an
    "ok=True" verdict — fail-open. The validator is a backstop, not a hard
    gate; the schema + address checks already ran upstream.
    """
    api_key = (settings.OPRAI_OPENAI_API_KEY or "").strip()
    if not api_key:
        return ValidatorVerdict(True)

    global _CLIENT
    if _CLIENT is None:
        try:
            from openai import AsyncOpenAI

            _CLIENT = AsyncOpenAI(api_key=api_key)
        except Exception:
            logger.debug("output_validator: OpenAI init failed", exc_info=True)
            return ValidatorVerdict(True)

    if isinstance(tool_args, str):
        try:
            args = json.loads(tool_args)
        except json.JSONDecodeError:
            return ValidatorVerdict(
                False,
                reason="tool args were not valid JSON",
                severity="block",
            )
    else:
        args = tool_args

    payload = {
        "user_message": user_message,
        "tool_name": tool_name,
        "tool_args": args,
    }

    try:
        resp = await _CLIENT.chat.completions.create(
            model=settings.OPRAI_SUMMARIZER_MODEL,  # reuse the cheap model
            messages=[
                {"role": "system", "content": _VALIDATOR_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            max_tokens=120,
            temperature=0.0,
        )
    except Exception:
        logger.debug("output_validator call failed — failing open", exc_info=True)
        return ValidatorVerdict(True)

    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        return ValidatorVerdict(True)
    try:
        verdict = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("output_validator returned non-JSON: %r", raw[:200])
        return ValidatorVerdict(True)
    if not isinstance(verdict, dict):
        return ValidatorVerdict(True)

    ok = bool(verdict.get("ok", True))
    reason = str(verdict.get("reason", "") or "")
    severity = str(verdict.get("severity", "") or "")
    if not ok and severity not in ("block", "warn"):
        # Defensive default — if the validator says "not ok" but doesn't pick
        # a severity, treat as a warning rather than blocking the user.
        severity = "warn"
    return ValidatorVerdict(ok=ok, reason=reason, severity=severity)
