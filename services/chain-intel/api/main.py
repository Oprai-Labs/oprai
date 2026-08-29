"""chain-intel API — exposes Robinhood-Chain analysis objects to OPRAI.

Read-only over the ClickHouse index. Each endpoint returns a structured object
{ subject, status, kpis, charts, tables, facts } that OPRAI's LLM narrates into a
long report and the frontend renders as charts/tables. Internal service — sits
behind the gateway, called by chat-service; guard with X-Internal-Api-Key.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException, Query

from . import ch, reports

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
