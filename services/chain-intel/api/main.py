"""chain-intel API — exposes Robinhood-Chain analysis objects to OPRAI.

Read-only over the ClickHouse index. Each endpoint returns a structured object
{ subject, status, kpis, charts, tables, facts } that OPRAI's LLM narrates into a
long report and the frontend renders as charts/tables. Internal service — sits
behind the gateway, called by chat-service; guard with X-Internal-Api-Key.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException, Query

from . import ch, reports, signals

app = FastAPI(title="OPRAI chain-intel", version="0.1.0")
_KEY = os.environ.get("OPRAI_INTERNAL_API_KEY", "")


def _gate(x_internal_api_key: str | None):
    if _KEY and x_internal_api_key != _KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
async def health():
    try:
        blocks = await ch.scalar("SELECT max(number) FROM rh.blocks")
        return {"ok": True, "max_block": blocks}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.get("/token/{token}")
async def token(token: str, x_internal_api_key: str | None = Header(None)):
    _gate(x_internal_api_key)
    if not ch.is_addr(token):
        raise HTTPException(400, "token must be a 0x address")
    return await reports.token_report(token)


@app.get("/wallet/{wallet}")
async def wallet(wallet: str, x_internal_api_key: str | None = Header(None)):
    _gate(x_internal_api_key)
    if not ch.is_addr(wallet):
        raise HTTPException(400, "wallet must be a 0x address")
    return await reports.wallet_report(wallet)


@app.get("/smart-money")
async def smart_money(limit: int = Query(50, ge=1, le=200),
                      x_internal_api_key: str | None = Header(None)):
    _gate(x_internal_api_key)
    return await reports.smart_money(limit)


@app.get("/early-catchers/{token}")
async def early_catchers(token: str, max_price: float | None = Query(None),
                         limit: int = Query(50, ge=1, le=200),
                         x_internal_api_key: str | None = Header(None)):
    _gate(x_internal_api_key)
    if not ch.is_addr(token):
        raise HTTPException(400, "token must be a 0x address")
    return await reports.early_catchers(token, max_price, limit)


@app.get("/screen")
async def screen(min_pnl: float | None = Query(None), min_win_rate: float | None = Query(None),
                 min_tokens: int | None = Query(None), limit: int = Query(40, ge=1, le=200),
                 x_internal_api_key: str | None = Header(None)):
    _gate(x_internal_api_key)
    return await reports.screen(min_pnl, min_win_rate, min_tokens, limit)


@app.get("/cohort/{token}")
async def cohort(token: str, window_days: int = Query(7, ge=1, le=90),
                 limit: int = Query(20, ge=1, le=100),
                 x_internal_api_key: str | None = Header(None)):
    """What are this token's whales buying next? — follow the cohort's money."""
    _gate(x_internal_api_key)
    if not ch.is_addr(token):
        raise HTTPException(400, "token must be a 0x address")
    return await reports.cohort_flow(token, window_days, limit)


@app.get("/honeypot/{token}")
async def honeypot(token: str, amount: float | None = Query(None),
                   x_internal_api_key: str | None = Header(None)):
    """Can you exit this token? Resolves the live Pons curve via the node."""
    _gate(x_internal_api_key)
    if not ch.is_addr(token):
        raise HTTPException(400, "token must be a 0x address")
    return await reports.honeypot(token, amount)


# ── Real-time alpha signals (Telegram alert engine polls these) ────────────────

@app.get("/signals/tip")
async def signals_tip(x_internal_api_key: str | None = Header(None)):
    """The index cursor ceiling — the bot seeds each subscription's since_block here."""
    _gate(x_internal_api_key)
    return {"tip": await signals.index_tip()}


@app.get("/signals/smart-buys")
async def signals_smart_buys(since_block: int = Query(..., ge=0),
                             min_smart: int = Query(2, ge=1, le=50),
                             limit: int = Query(30, ge=1, le=100),
                             x_internal_api_key: str | None = Header(None)):
    """Discovery feed: tokens smart wallets bought since `since_block`."""
    _gate(x_internal_api_key)
    return await signals.smart_buys(since_block, min_smart, limit)


@app.get("/signals/new-launches")
async def signals_new_launches(since_block: int = Query(..., ge=0),
                               with_smart_only: bool = Query(False),
                               limit: int = Query(40, ge=1, le=100),
                               x_internal_api_key: str | None = Header(None)):
    """Fresh launches since `since_block` (optionally only those smart money bought)."""
    _gate(x_internal_api_key)
    return await signals.new_launches(since_block, with_smart_only, limit)


@app.get("/wallet/{wallet}/recent-buys")
async def wallet_recent_buys(wallet: str, since_block: int = Query(..., ge=0),
                             limit: int = Query(20, ge=1, le=100),
                             x_internal_api_key: str | None = Header(None)):
    """A tracked wallet's buys since `since_block` — for per-wallet alerts."""
    _gate(x_internal_api_key)
    if not ch.is_addr(wallet):
        raise HTTPException(400, "wallet must be a 0x address")
    return await signals.wallet_recent_buys(wallet, since_block, limit)
