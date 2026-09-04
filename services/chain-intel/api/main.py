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


@app.get("/signals/smart-flow")
async def signals_smart_flow(since_block: int = Query(..., ge=0),
                             min_smart: int = Query(1, ge=1, le=50),
                             limit: int = Query(30, ge=1, le=100),
                             x_internal_api_key: str | None = Header(None)):
    """Smart-money buys vs SELLS per token since `since_block` (accumulating / distributing)."""
    _gate(x_internal_api_key)
    return await signals.smart_flow(since_block, min_smart, limit)


@app.get("/signals/token-buyers/{token}")
async def signals_token_buyers(token: str, since_block: int = Query(0, ge=0),
                               limit: int = Query(25, ge=1, le=100),
                               x_internal_api_key: str | None = Header(None)):
    """WHICH smart wallets bought this token, with why each is smart."""
    _gate(x_internal_api_key)
    if not ch.is_addr(token):
        raise HTTPException(400, "token must be a 0x address")
    return await signals.token_smart_buyers(token, since_block, limit)


@app.get("/wallet/{wallet}/balances")
async def wallet_balances(wallet: str, limit: int = Query(60, ge=1, le=200),
                          x_internal_api_key: str | None = Header(None)):
    """LIVE holdings + per-token USD value + portfolio total (node balanceOf)."""
    _gate(x_internal_api_key)
    if not ch.is_addr(wallet):
        raise HTTPException(400, "wallet must be a 0x address")
    return await signals.wallet_balances(wallet, limit)


@app.get("/wallet/{wallet}/positions")
async def wallet_positions(wallet: str, x_internal_api_key: str | None = Header(None)):
    """Protocol positions (Uniswap V3/V4 LP, Morpho Blue, ERC-4626 vaults, staking,
    Ramses pools, Lighter) priced in USD, read from our node."""
    _gate(x_internal_api_key)
    if not ch.is_addr(wallet):
        raise HTTPException(400, "wallet must be a 0x address")
    from . import positions
    return await positions.wallet_positions(wallet)


@app.get("/decode/tx/{tx_hash}")
async def decode_tx(tx_hash: str, x_internal_api_key: str | None = Header(None)):
    """One receipt → every swap leg (venue, pool key, tokens, amounts) and the sender's
    net buy/sell with USD — read from the node, for copy-trade decisions."""
    _gate(x_internal_api_key)
    if not (tx_hash.startswith("0x") and len(tx_hash) == 66):
        raise HTTPException(400, "tx_hash must be a 0x…64-hex hash")
    from . import decode
    return await decode.decode_tx(tx_hash.lower())


@app.get("/simulate/sell/{token}")
async def simulate_sell(token: str, usd: float = Query(10.0, gt=0, le=100000),
                        x_internal_api_key: str | None = Header(None)):
    """Can this token be sold right now and at what cost? Quotes a small sell and buy
    through the venue's own quoter (V4Quoter / QuoterV2 / Pons curve) vs spot."""
    _gate(x_internal_api_key)
    if not ch.is_addr(token):
        raise HTTPException(400, "token must be a 0x address")
    from . import decode
    return await decode.simulate_sell(token, usd)


@app.get("/wallet/{wallet}/smart-profile")
async def wallet_smart_profile(wallet: str, x_internal_api_key: str | None = Header(None)):
    """WHY a wallet is (or isn't) smart — rank, PnL, win rate, tokens; EOA check."""
    _gate(x_internal_api_key)
    if not ch.is_addr(wallet):
        raise HTTPException(400, "wallet must be a 0x address")
    return await signals.wallet_smart_profile(wallet)
