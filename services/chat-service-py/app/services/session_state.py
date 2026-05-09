"""Per-session high-level state extraction.

The block summariser compresses *what happened*; this module tracks *where
we are*. After every assistant turn we ask the summariser model for a small
JSON object describing the user's current intent, active protocol, candidate
entities they've narrowed down to, and any pending decision. That object is
stored on `chat_sessions.session_state` and re-injected on every subsequent
turn — so the responder sees a stable, structured source of truth even after
120 messages of raw history have been compressed away.

Without this, the responder is forced to rely on prose summaries that are
prone to losing exact addresses (vanity-prefix attack surface), drifting in
intent, and inheriting transient failure states.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update

from app.config import settings
from app.models.session import ChatSession
import uuid as _uuid

logger = logging.getLogger(__name__)


# Lazy-init OpenAI client; reused across calls.
_CLIENT: Any = None


_EXTRACT_PROMPT = """You maintain a structured "session state" for a Solana DeFi assistant.
The model uses this state on every turn as the source of truth for what the
user is trying to do, so be precise and conservative — empty fields are
better than guessed ones.

Input: the **previous** session_state (may be empty), plus the most recent
user message and the assistant reply.

Output: a single JSON object with this exact shape (every key present):

{
  "current_intent":   "<short phrase, e.g. 'add_liquidity_meteora_dlmm', 'swap_jupSOL_to_usdc', 'browse_defi_yields'>",
  "active_protocol":  "<lowercase protocol id (jupiter, meteora, kamino, marginfi, raydium, orca, pumpfun, marinade, jito, jupsol, '') >",
  "candidate_entities": [
    {"kind": "<pool|token|wallet|validator>", "label": "<symbol or short name>",
     "address": "<mint/pool/wallet base58 if known, else ''>",
     "metric": "<TVL|APY|price|''>", "value": "<string or number, original units>"}
  ],
  "selected_entity": {"kind": "...", "label": "...", "address": "..."} | null,
  "pending_decision": "<short phrase: what the user must answer next, or '' if none>",
  "wallet_snapshot":  {"<symbol>": "<balance string>", ...},
  "blockers": ["<unresolved durable blocker>", ...]
}

Rules:
1. **Carry forward the previous state** unless the new turn changes a field. If
   the user is still browsing the same set of pools, keep `candidate_entities`
   exactly as you received it. Don't drop facts that haven't been refuted.
2. **Update `selected_entity` the moment the user picks one** ("the highest
   TVL one", "the second", "JupSOL-JitoSOL"). Resolve ordinal/superlative
   references against the existing `candidate_entities`. Never invent an
   address that wasn't in candidate_entities or the latest assistant reply.
3. **Drop transient blockers.** Only items the user can't currently bypass
   (no wallet connected, insufficient balance) belong in `blockers`. A
   one-time API error from earlier is not a blocker.
4. Return `null` for unset fields, not the string "null".
5. Output only the JSON. No commentary, no fences, no preamble."""


async def update_session_state(
    db: AsyncSession,
    session_id: str,
    previous_state: dict | None,
    user_text: str,
    assistant_text: str,
) -> dict:
    """Compute and persist the new session_state for `session_id`.

    Returns the updated state dict (possibly equal to `previous_state` when
    extraction fails — we never wipe a known-good state with a partial one).
    """
    api_key = (settings.OPRAI_OPENAI_API_KEY or "").strip()
    if not api_key:
        return previous_state or {}

    global _CLIENT
    if _CLIENT is None:
        try:
            from openai import AsyncOpenAI

            _CLIENT = AsyncOpenAI(api_key=api_key)
        except Exception:
            logger.warning("session_state: OpenAI init failed", exc_info=True)
            return previous_state or {}

    payload = {
        "previous_state": previous_state or {},
        "latest_user_message": user_text,
        "latest_assistant_message": assistant_text,
    }
    try:
        resp = await _CLIENT.chat.completions.create(
            model=settings.OPRAI_SUMMARIZER_MODEL,
            messages=[
                {"role": "system", "content": _EXTRACT_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            max_tokens=600,
            temperature=0.0,
        )
    except Exception:
        logger.warning("session_state extraction failed", exc_info=True)
        return previous_state or {}

    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        return previous_state or {}
    try:
        new_state = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("session_state extractor returned non-JSON: %r", raw[:200])
        return previous_state or {}
    if not isinstance(new_state, dict):
        return previous_state or {}

    try:
        await db.execute(
            update(ChatSession)
            .where(ChatSession.id == _uuid.UUID(session_id))
            .values(session_state=new_state)
        )
    except Exception:
        logger.warning("session_state persist failed", exc_info=True)
        return new_state  # in-memory copy still usable for this turn

    return new_state


def _candidates_match_message(candidates: list, user_message: str) -> bool:
    """Heuristic: do any candidate labels overlap with tokens in the user message?

    The session_state can carry stale candidates from earlier turns (e.g. the
    user explored JupSOL-X pools, selected one, then asks about a totally
    different pair on the next turn). When the candidates have nothing in
    common with the current message, injecting them poisons the LLM — it
    treats the stale list as authoritative and answers from it instead of
    re-querying.

    Returns True when at least one alphanumeric token (length ≥ 3) from the
    user message appears (case-insensitive) inside any candidate's label or
    address. False means "candidates look unrelated to current intent" —
    caller should skip injection.

    The check is intentionally lenient: a token-pair message like "jupsol-sol"
    matches a "JupSOL-JitoSOL" candidate via the "jupsol" overlap, even
    though the second token differs. That false-positive is fine — we're
    only trying to drop *completely* unrelated candidate sets.
    """
    if not user_message:
        return True  # No way to tell — keep the candidates.
    tokens = {t.lower() for t in re.findall(r"[A-Za-z0-9]{3,}", user_message)}
    if not tokens:
        return True
    haystack = " ".join(
        f"{c.get('label', '')} {c.get('address', '')}".lower()
        for c in candidates
        if isinstance(c, dict)
    )
    return any(tok in haystack for tok in tokens)


def render_session_state_for_llm(
    state: dict | None,
    user_message: str = "",
    intent: str | None = None,
) -> str:
    """Format `state` into a single system message the responder reads.

    Returns "" when there's nothing to inject — caller can skip the message.
    Hand-rolled rendering (rather than dumping JSON) so the responder doesn't
    drift into JSON output mode itself.

    `user_message` is the message we're about to answer. We use it to drop
    stale candidate lists that have nothing to do with the current intent —
    see `_candidates_match_message`. Empty string is a fallback that keeps
    everything (legacy behaviour).

    `intent` is the IntentRouter classification for the current message. When
    the intent is not "action", action-specific fields (Pending, Selected,
    Candidates) are suppressed — they belong to the previous turn's flow and
    must not cause the model to execute a stale transaction instead of
    answering the user's actual info question.
    """
    if not state or not isinstance(state, dict):
        return ""

    # When the current intent is clearly informational (query / advice /
    # ambiguous), the "Pending" + "Selected" + "Candidates" context from a
    # previous action turn is stale. Injecting it makes the model treat the
    # info question as confirmation of the old action (e.g. "jitoSOL APR
    # nedir?" gets treated as "confirm the pending add_liquidity" because
    # "jitosol" matches the candidate label). Suppress action-context fields
    # so the model starts fresh on a new topic.
    action_context_allowed = intent in (None, "action")

    parts: list[str] = ["[Session State — prior selections snapshot]"]
    # Only surface the stored intent when we are in an action turn — otherwise
    # the stale intent (e.g. "add_liquidity_jitosol_dlmm") anchors the model to
    # the wrong task when the user switches to an info question.
    if action_context_allowed:
        if (stored_intent := state.get("current_intent")):
            parts.append(f"User intent: {stored_intent}")
    if (proto := state.get("active_protocol")):
        parts.append(f"Active protocol: {proto}")

    if action_context_allowed:
        raw_candidates = state.get("candidate_entities") or []
        candidates_relevant = (
            isinstance(raw_candidates, list)
            and raw_candidates
            and _candidates_match_message(raw_candidates, user_message)
        )
        if candidates_relevant:
            rendered = []
            for ent in raw_candidates[:10]:
                if not isinstance(ent, dict):
                    continue
                kind = ent.get("kind", "?")
                label = ent.get("label", "?")
                addr = ent.get("address") or ""
                metric = ent.get("metric") or ""
                value = ent.get("value") or ""
                seg = f"{kind}:{label}"
                if addr:
                    seg += f" ({addr})"
                if metric and value:
                    seg += f" {metric}={value}"
                rendered.append(seg)
            if rendered:
                parts.append("Candidates: " + " | ".join(rendered))

        sel = state.get("selected_entity")
        if isinstance(sel, dict) and sel:
            kind = sel.get("kind", "?")
            label = sel.get("label", "?")
            addr = sel.get("address") or ""
            seg = f"{kind}:{label}"
            if addr:
                seg += f" ({addr})"
            parts.append(f"Selected: {seg}")

        if (pending := state.get("pending_decision")):
            parts.append(f"Pending: {pending}")

    snapshot = state.get("wallet_snapshot") or {}
    if isinstance(snapshot, dict) and snapshot:
        bits = [f"{k}={v}" for k, v in list(snapshot.items())[:8]]
        parts.append("Wallet: " + ", ".join(bits))

    blockers = state.get("blockers") or []
    if isinstance(blockers, list) and blockers:
        parts.append("Blockers: " + "; ".join(str(b) for b in blockers[:4]))

    # Just the header — nothing real to inject.
    if len(parts) == 1:
        return ""
    return "\n".join(parts)
