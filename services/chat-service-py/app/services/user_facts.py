"""Structured per-user memory (ChatGPT-style "Memory" feature).

The summariser tracks *what was discussed*; this module tracks *who the user
is and what they want by default*. After every assistant turn, a small LLM
call extracts discrete facts ("user prefers conservative DeFi strategies",
"default slippage = 100 bps", "always sign with Phantom") and upserts them
into `chat_schema.user_facts`. On every subsequent turn — *including across
sessions* — the facts are rendered into a single system message so the
responder honours them without the user having to repeat themselves.

Why this and not vector recall:
    • Vector recall over raw chat history is noisy: similar-but-irrelevant
      messages get pulled in, attention dilutes, hallucinations get worse.
    • Curated facts are tiny (each ~50 tokens) so the per-turn cost is
      negligible. ChatGPT does it this way for the same reason.
    • Facts are typed (`fact_type`) so the renderer can lay them out
      consistently regardless of how the model phrased them.

Storage: `chat_schema.user_facts(wallet, fact_type, fact_value JSONB,
confidence, source, ...)`. UNIQUE(wallet, fact_type) means each fact_type
is single-valued per wallet — new extractions OVERWRITE the old value
rather than accumulating duplicates.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)


_CLIENT: Any = None


# Whitelisted fact types. The extractor model is told to emit only these;
# anything else is rejected before it touches the database. Keeps the
# memory surface area predictable and avoids prompt-injected "facts".
_ALLOWED_TYPES: frozenset[str] = frozenset({
    "risk_tolerance",        # "conservative" | "balanced" | "aggressive"
    "preferred_wallet",      # "phantom" | "solflare" | "backpack" | "ledger"
    "default_slippage_bps",  # int
    "max_position_size_usd", # float
    "preferred_dex",         # "jupiter" | "orca" | "raydium" | "meteora"
    "preferred_lender",      # "kamino" | "jupiter"
    "usually_holds",         # ["SOL", "USDC", "JITOSOL"]
    "alerts_off",            # bool
    "name_or_handle",        # display name or twitter handle the user mentioned
    "trading_style",         # "swing" | "scalper" | "long_term" | "yield_farmer"
    "avoids_protocols",      # ["pumpfun", "memecoin"]
})


_EXTRACTOR_PROMPT = f"""You maintain durable per-user preferences for a Solana DeFi assistant.
Read the latest user message and the assistant reply, then return any
*durable* preferences that emerge — facts that should hold for THIS user
across sessions, not just within one turn.

Allowed fact_type values (anything else is rejected, do NOT invent new types):
  {", ".join(sorted(_ALLOWED_TYPES))}

Output: a single JSON object:

{{
  "facts": [
    {{"fact_type": "<one of the allowed types>",
      "fact_value": <JSON value matching that fact's expected shape>,
      "confidence": <float 0.0–1.0>}}
  ]
}}

Rules:
1. Empty facts array is the right answer most of the time. Only emit a fact
   when the user *explicitly* expressed a preference or a setting. Do NOT
   infer preferences from a single trade ("they bought $TRUMP once" is NOT
   evidence of preferred_dex or trading_style).
2. confidence = 1.0 for direct statements ("I always use Phantom"),
   ~0.7 for strong implications, ~0.4 for inferences. Anything <0.4
   should not be emitted.
3. Each fact_value must match its type:
   * risk_tolerance / preferred_wallet / preferred_dex / trading_style /
     preferred_lender / language: a string
   * default_slippage_bps: integer (basis points, 0–3000)
   * max_position_size_usd: number (positive)
   * usually_holds / avoids_protocols: array of strings
   * alerts_off: boolean
   * name_or_handle: string (no @, no URLs)
4. Do NOT include sensitive PII (real names, emails, phone numbers, exact
   wallet balances, transaction signatures). These are operational data,
   not preferences.
5. Output JSON only. No commentary, no markdown.
6. If the user explicitly says to forget, remove, or stop a preference (e.g. "forget my DEX preference", "don't use Meteora anymore", "remove my slippage setting"), emit the fact with confidence=0.0 and fact_value=null. The caller will delete zero-confidence facts."""



# Wallets and venues a preference could plausibly name. Not a curated
# allowlist of what the product supports — a check that the extractor wrote a
# *name of the right kind*. It once stored preferred_wallet = "cüzdan", which
# is simply the Turkish word for "wallet": the model saw the noun in the
# sentence and recorded it as the answer.
_KNOWN_WALLETS: frozenset[str] = frozenset({
    "phantom", "solflare", "backpack", "ledger", "trezor", "trust", "exodus",
    "glow", "coinbase", "okx", "bitget", "magiceden", "magic eden", "tiplink",
})
_KNOWN_VENUES: frozenset[str] = frozenset({
    "jupiter", "raydium", "orca", "meteora", "phoenix", "openbook", "lifinity",
    "pumpfun", "pump.fun", "pumpswap", "kamino", "marginfi", "solend", "save",
    "drift", "marinade", "jito", "sanctum", "aldrin", "saber", "invariant",
})

def _plausible(fact_type: str, value: object) -> bool:
    """Whether a value is the right *kind* of thing for its fact type.

    The extractor is told to emit facts only from explicit statements, and it
    ignores that instruction often enough to matter — every wrong fact found on
    a live account had been written with confidence 1.0, so the confidence
    floor never saw them. These are the shapes that actually went wrong.
    """
    if fact_type == "preferred_wallet":
        return isinstance(value, str) and value.strip().lower() in _KNOWN_WALLETS

    if fact_type in ("preferred_dex", "preferred_lender"):
        return isinstance(value, str) and value.strip().lower() in _KNOWN_VENUES

    if fact_type == "usually_holds":
        # Holdings are tokens. A live account had ["Meteora", "Raydium",
        # "Pump.fun"] here — venues recorded as though they were assets, which
        # then read back as "this user usually holds Meteora".
        if not isinstance(value, list) or not value:
            return False
        return not any(
            isinstance(v, str) and v.strip().lower() in _KNOWN_VENUES for v in value
        )

    if fact_type == "max_position_size_usd":
        # Nobody states a maximum position size of one cent. That figure came
        # from reading the balances of a wallet being used for testing, which
        # rule 1 of the extractor prompt forbids and the model did anyway.
        try:
            return float(value) >= 1.0  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False

    return True


async def extract_and_upsert(
    db: AsyncSession,
    wallet: str,
    user_text: str,
    assistant_text: str,
) -> int:
    """Run the extractor on the latest exchange and upsert any facts found.

    Returns the number of facts written. Best-effort: any failure (model
    error, malformed JSON, type mismatch) results in 0 writes — never
    raises into the chat flow.
    """
    if not wallet:
        return 0
    api_key = (settings.OPRAI_OPENAI_API_KEY or "").strip()
    if not api_key:
        return 0

    global _CLIENT
    if _CLIENT is None:
        try:
            from openai import AsyncOpenAI

            _CLIENT = AsyncOpenAI(api_key=api_key)
        except Exception:
            logger.debug("user_facts: OpenAI init failed", exc_info=True)
            return 0

    payload = {
        "latest_user_message": user_text,
        "latest_assistant_message": assistant_text,
    }
    try:
        resp = await _CLIENT.chat.completions.create(
            model=settings.OPRAI_SUMMARIZER_MODEL,
            messages=[
                {"role": "system", "content": _EXTRACTOR_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            max_tokens=400,
            temperature=0.0,
        )
    except Exception:
        logger.debug("user_facts extractor failed", exc_info=True)
        return 0

    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        return 0
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("user_facts extractor non-JSON: %r", raw[:200])
        return 0

    facts = body.get("facts") if isinstance(body, dict) else None
    if not isinstance(facts, list):
        return 0

    written = 0
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        fact_type = str(fact.get("fact_type") or "").strip()
        if fact_type not in _ALLOWED_TYPES:
            continue
        if "fact_value" not in fact:
            continue
        try:
            confidence = float(fact.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        # Manual override: user explicitly asked to forget this fact.
        if confidence == 0.0 and fact.get("fact_value") is None:
            try:
                await db.execute(
                    sa_text(
                        f"DELETE FROM {settings.DB_SCHEMA}.user_facts "
                        "WHERE wallet = :wallet AND fact_type = :fact_type"
                    ),
                    {"wallet": wallet, "fact_type": fact_type},
                )
                written += 1
            except Exception:
                logger.debug("user_facts delete failed for %s", fact_type, exc_info=True)
            continue

        if confidence < 0.4:
            continue
        if not _plausible(fact_type, fact["fact_value"]):
            logger.info(
                "user_facts rejected implausible %s=%r (confidence %.2f)",
                fact_type,
                fact["fact_value"],
                confidence,
            )
            continue
        confidence = min(max(confidence, 0.0), 1.0)

        try:
            await db.execute(
                sa_text(
                    f"""
                    INSERT INTO {settings.DB_SCHEMA}.user_facts
                        (id, wallet, fact_type, fact_value, confidence, source, updated_at)
                    VALUES (gen_random_uuid(), :wallet, :fact_type,
                            CAST(:fact_value AS JSONB), :confidence, 'extracted', now())
                    ON CONFLICT (wallet, fact_type) DO UPDATE
                      SET fact_value = EXCLUDED.fact_value,
                          -- The newest reading wins outright. Taking the
                          -- greater of the two ratcheted confidence upwards
                          -- and never down, so a fact written wrongly at 1.0
                          -- could never be softened by anything later.
                          confidence = EXCLUDED.confidence,
                          updated_at = now()
                    """
                ),
                {
                    "wallet": wallet,
                    "fact_type": fact_type,
                    "fact_value": json.dumps(fact["fact_value"]),
                    "confidence": confidence,
                },
            )
            written += 1
        except Exception:
            logger.debug("user_facts upsert failed for %s", fact_type, exc_info=True)
            continue

    return written


async def decay_stale_facts(db: AsyncSession, wallet: str) -> None:
    """Decay confidence of facts not updated in 90 days and delete those that hit 0.

    Intended to run once per session (not per turn). Silent on any failure.
    """
    if not wallet:
        return
    try:
        # Decrease confidence by 0.3 for stale facts, floored at 0.
        await db.execute(
            sa_text(
                f"""
                UPDATE {settings.DB_SCHEMA}.user_facts
                SET confidence = GREATEST(0.0, confidence - 0.3)
                WHERE wallet = :wallet
                  AND updated_at < now() - INTERVAL '90 days'
                """
            ),
            {"wallet": wallet},
        )
        # Delete facts whose confidence has now reached 0.
        await db.execute(
            sa_text(
                f"""
                DELETE FROM {settings.DB_SCHEMA}.user_facts
                WHERE wallet = :wallet
                  AND confidence <= 0.0
                """
            ),
            {"wallet": wallet},
        )
        await db.commit()
    except Exception:
        logger.debug("user_facts decay skipped for wallet=%s", wallet, exc_info=True)
        await db.rollback()


async def render_for_llm(db: AsyncSession, wallet: str) -> str:
    """Format this wallet's stored facts into a single system message.

    Returns "" when the wallet has no facts — the caller skips injecting an
    empty block. Limits the output to the most recently updated 12 facts so
    a noisy extractor can't bloat every prompt.
    """
    if not wallet:
        return ""
    try:
        rows = await db.execute(
            sa_text(
                f"""
                SELECT fact_type, fact_value, confidence
                FROM {settings.DB_SCHEMA}.user_facts
                WHERE wallet = :wallet
                  AND confidence >= 0.4
                ORDER BY updated_at DESC
                LIMIT 12
                """
            ),
            {"wallet": wallet},
        )
        items = rows.fetchall()
    except Exception:
        logger.debug("user_facts render: query failed", exc_info=True)
        return ""

    if not items:
        return ""

    parts: list[str] = ["[User Memory — durable preferences across sessions]"]
    for ft, fv, conf in items:
        # Rows written before `language` was dropped as a fact type. Reply
        # language follows the message in front of the model, never a stored
        # preference — see `_last_spoken_message` in message.py.
        if ft == "language":
            continue
        # JSONB → Python type
        if isinstance(fv, (dict, list)):
            value_str = json.dumps(fv, ensure_ascii=False)
        else:
            value_str = str(fv)
        # Compact tag-value
        confidence_marker = "" if conf >= 0.85 else f" (conf={conf:.1f})"
        parts.append(f"- {ft}: {value_str}{confidence_marker}")
    parts.append(
        "Honour these preferences by default; deviate only if the current "
        "message contradicts them."
    )
    return "\n".join(parts)
