"""Durable per-LLM-call token + USD-cost ledger.

Writes `chat_schema.llm_usage` (event-level) and upserts `chat_schema.llm_usage_daily`
(per wallet/day/model). Runs on ITS OWN DB session so a ledger failure can never
poison or roll back the chat-response transaction, and swallows every error
(fire-and-forget) — analytics must never break a reply.

Cost is looked up from `chat_schema.model_pricing` and frozen on the row, so a
later price change does not rewrite history. Cached tokens are billed at the
cache rate instead of full input.
"""

import logging
import uuid as _uuid
from typing import Optional

from sqlalchemy import text

from app.db.connection import async_session_factory

_log = logging.getLogger(__name__)


async def record_llm_usage(
    *,
    wallet: str,
    session_id: Optional[str],
    model: str,
    request_kind: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
    is_estimated: bool = False,
) -> None:
    prompt_tokens = max(0, int(prompt_tokens or 0))
    completion_tokens = max(0, int(completion_tokens or 0))
    cached_tokens = max(0, int(cached_tokens or 0))
    total = prompt_tokens + completion_tokens
    if total <= 0 or not wallet:
        return

    sid: Optional[str] = None
    if session_id:
        try:
            sid = str(_uuid.UUID(str(session_id)))
        except Exception:
            sid = None

    try:
        async with async_session_factory() as s:
            row = (
                await s.execute(
                    text(
                        """SELECT input_usd_per_1m, output_usd_per_1m, cached_usd_per_1m
                           FROM chat_schema.model_pricing WHERE model = :m"""
                    ),
                    {"m": model},
                )
            ).first()

            cost: Optional[float]
            if row is not None:
                in_p, out_p, cache_p = float(row[0]), float(row[1]), float(row[2])
                # prompt_tokens is already the FRESH (billable) input and
                # cached_tokens the cache-read portion — the two are disjoint
                # (normalized at the call site for both providers), so no
                # subtraction: fresh @ input rate, cache-read @ cache rate.
                cost = (
                    prompt_tokens * in_p
                    + completion_tokens * out_p
                    + cached_tokens * cache_p
                ) / 1_000_000.0
            else:
                cost = None  # unknown model — keep the row, leave cost NULL

            await s.execute(
                text(
                    """
                    INSERT INTO chat_schema.llm_usage
                      (wallet, session_id, model, request_kind, prompt_tokens,
                       completion_tokens, cached_tokens, total_tokens, cost_usd, is_estimated)
                    VALUES (:w, :sid, :m, :k, :p, :c, :cache, :tot, :cost, :est)
                    """
                ),
                {
                    "w": wallet, "sid": sid, "m": model, "k": request_kind,
                    "p": prompt_tokens, "c": completion_tokens, "cache": cached_tokens,
                    "tot": total, "cost": cost, "est": is_estimated,
                },
            )

            await s.execute(
                text(
                    """
                    INSERT INTO chat_schema.llm_usage_daily
                      (wallet, stat_date, model, prompt_tokens, completion_tokens,
                       cached_tokens, requests, cost_usd, updated_at)
                    VALUES (:w, (NOW() AT TIME ZONE 'UTC')::date, :m, :p, :c, :cache, 1, :cost, NOW())
                    ON CONFLICT (wallet, stat_date, model) DO UPDATE SET
                      prompt_tokens     = llm_usage_daily.prompt_tokens     + EXCLUDED.prompt_tokens,
                      completion_tokens = llm_usage_daily.completion_tokens + EXCLUDED.completion_tokens,
                      cached_tokens     = llm_usage_daily.cached_tokens     + EXCLUDED.cached_tokens,
                      requests          = llm_usage_daily.requests          + 1,
                      cost_usd          = llm_usage_daily.cost_usd          + EXCLUDED.cost_usd,
                      updated_at        = NOW()
                    """
                ),
                {
                    "w": wallet, "m": model, "p": prompt_tokens, "c": completion_tokens,
                    "cache": cached_tokens, "cost": cost or 0,
                },
            )
            await s.commit()
    except Exception:
        _log.debug("record_llm_usage failed", exc_info=True)
