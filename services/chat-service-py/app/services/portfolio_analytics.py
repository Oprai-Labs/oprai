"""Per-wallet cost-basis aggregator.

Walks a wallet's Helius enhanced transaction history, marks every wallet-side
token transfer as a buy or a sale, prices each leg at the transaction's
historical USD value (Birdeye `/defi/history_price`), and persists the
running totals into `chat_schema.wallet_token_costbasis`. The route layer
reads this table and computes the all-time PnL per holding at request time
(no cached PnL — current prices move and would invalidate stored deltas).

Idempotency
-----------
Every wallet has one row in `wallet_costbasis_sync` carrying
`last_processed_signature`. Subsequent refreshes only scan signatures *newer
than* that pointer, so incremental syncs stay cheap once the initial
backfill is done. The pointer is moved forward only after a successful
batch — a partial / failed batch leaves the pointer where it was so a retry
re-processes the same range.

Pricing fallbacks
-----------------
* Stables (USDC, USDT, USDS, USDH, PYUSD, USDe) → flat $1.00; no API call.
* Wrapped SOL → price `So111…112` same call.
* Anything else → Birdeye `/defi/history_price?address=<mint>&type=1H&time_from=<t-60>&time_to=<t+60>`,
  pick nearest candle. Misses (rug pulls, brand-new memes outside Birdeye)
  contribute zero cost on the in-side and zero proceeds on the out-side so
  the aggregator stays consistent (PnL just reads `—` for that holding).

This service is intentionally pull-based: nothing here schedules itself.
The route `POST /portfolio/refresh/{wallet}` triggers it (debounced
5 min). The frontend optimistically renders cached PnL while a fresh sync
runs in the background.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger(__name__)


HELIUS_KEY = os.environ.get("HELIUS_API_KEY", "")
# The RPC node, not api.helius.xyz. Helius Cloudflare-blocks this server's IP
# on the enhanced-transactions REST host — every call came back as a 403 HTML
# page regardless of key or User-Agent, while the same key answers normally
# from another address. The RPC host is unaffected, so history is rebuilt from
# it instead. See _helius_get_txs.
HELIUS_RPC = "https://mainnet.helius-rpc.com/?api-key=" + HELIUS_KEY
BIRDEYE_KEY = os.environ.get("BIRDEYE_API_KEY", "")
BIRDEYE_BASE = "https://public-api.birdeye.so"

# Mints whose price is always $1. Skipping the Birdeye call here saves
# 90%+ of the price-lookup latency for any wallet that swaps in/out of
# stables (most do). Add new stables conservatively — a depegged token
# would propagate wrong cost basis silently.
STABLE_MINTS: set[str] = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "USDSwr9ApdHk5bvJKMjzff41FfuX8bSxdKcR81vTwcA",   # USDS
    "USDH1SM1ojwWUga67PGrgFWUHibbjqMvuMaDkRJTgkX",   # USDH
    "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo",  # PYUSD
    "DEhAasscXF4kEGxFgJ3bq4PpVGp5wyUxMRvn6TzGVHaw",  # USDe
}

SOL_MINT = "So11111111111111111111111111111111111111112"

# Page size for Helius enhanced-tx pagination. Helius caps a single call
# at 100 transactions; we paginate via the `before=<signature>` cursor.
HELIUS_PAGE = 100

# Hard cap on initial-backfill depth. A heavy DEX user can have 30k+ swaps;
# walking every page costs many seconds + many Birdeye calls and dilutes
# the PnL accuracy minimally past a year. Refreshes incrementally fill the
# delta in subsequent runs (idempotent on `last_signature`).
INITIAL_BACKFILL_PAGES = 30  # ~3000 txs


# Birdeye price cache for the lifetime of a single aggregator run. Same
# (mint, hour-bucket) is hit many times for high-volume traders — cache
# saves both wall time and the per-call rate limit budget.
_PriceKey = tuple[str, int]  # (mint, unix_hour)


@dataclass
class CostBasisDelta:
    """Single (mint, +/-amount, +/-usd) update applied to the running totals."""

    mint: str
    amount: float  # always positive
    usd: float     # always positive
    is_buy: bool   # True → bought, False → sold


def _rpc_batch(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Number a batch of JSON-RPC calls so replies can be matched back."""
    return [{**c, "jsonrpc": "2.0", "id": i} for i, c in enumerate(calls)]


def _tx_to_transfers(tx: dict[str, Any], wallet: str, signature: str) -> dict[str, Any]:
    """Rebuild the two transfer lists this module reads, from balance deltas.

    Only the wallet's own net movement matters here: every consumer compares
    from/toUserAccount against `wallet` and ignores the counterparty, so a
    balance delta answers the question directly. It is also the sturdier
    reading — a swap routed through several hops nets out to what the wallet
    actually gained and lost, with no intermediate legs to double count.
    """
    meta = tx.get("meta") or {}
    keys = [
        k.get("pubkey") if isinstance(k, dict) else k
        for k in ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys", [])
    ]

    native: list[dict[str, Any]] = []
    if wallet in keys:
        i = keys.index(wallet)
        pre = (meta.get("preBalances") or [])
        post = (meta.get("postBalances") or [])
        if i < len(pre) and i < len(post):
            delta = post[i] - pre[i]
            if delta:
                native.append({
                    "amount": abs(delta),
                    "toUserAccount": wallet if delta > 0 else None,
                    "fromUserAccount": wallet if delta < 0 else None,
                })

    by_mint: dict[str, float] = {}
    for field, sign in (("preTokenBalances", -1.0), ("postTokenBalances", 1.0)):
        for row in meta.get(field) or []:
            if row.get("owner") != wallet:
                continue
            mint = row.get("mint")
            amt = ((row.get("uiTokenAmount") or {}).get("uiAmount")) or 0.0
            if mint:
                by_mint[mint] = by_mint.get(mint, 0.0) + sign * float(amt)

    tokens = [
        {
            "mint": mint,
            "tokenAmount": abs(delta),
            "toUserAccount": wallet if delta > 0 else None,
            "fromUserAccount": wallet if delta < 0 else None,
        }
        for mint, delta in by_mint.items()
        if abs(delta) > 1e-12
    ]

    return {
        "signature": signature,
        "timestamp": tx.get("blockTime") or 0,
        "transactionError": meta.get("err"),
        "tokenTransfers": tokens,
        "nativeTransfers": native,
    }


async def _helius_get_txs(wallet: str, before: str | None = None) -> list[dict[str, Any]]:
    """One page of `wallet`'s history, newest first. Returns [] on failure.

    Built from plain JSON-RPC rather than the enhanced-transactions REST API,
    which Cloudflare-blocks this server's IP on (see HELIUS_RPC). Signatures
    come from getSignaturesForAddress — same newest-first order and the same
    `before` cursor the caller already pages with — then the transactions are
    fetched in one batched request rather than one round trip each.
    """
    if not HELIUS_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            opts: dict[str, Any] = {"limit": HELIUS_PAGE}
            if before:
                opts["before"] = before
            resp = await c.post(HELIUS_RPC, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getSignaturesForAddress",
                "params": [wallet, opts],
            })
            resp.raise_for_status()
            sigs = [s["signature"] for s in (resp.json().get("result") or []) if s.get("signature")]
            if not sigs:
                return []

            out: list[dict[str, Any]] = []
            # Chunked so one page never becomes a single oversized request.
            for i in range(0, len(sigs), 25):
                chunk = sigs[i:i + 25]
                batch = _rpc_batch([
                    {"method": "getTransaction", "params": [
                        s, {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"},
                    ]}
                    for s in chunk
                ])
                r = await c.post(HELIUS_RPC, json=batch)
                r.raise_for_status()
                replies = r.json()
                if not isinstance(replies, list):
                    continue
                # A batch reply may arrive out of order; `id` is the position
                # within this chunk, which is how each result finds its
                # signature again. Dropping a null result keeps the page
                # newest-first, so the caller's cursor stays valid.
                for reply in sorted(replies, key=lambda x: x.get("id", 0)):
                    tx = reply.get("result")
                    idx = reply.get("id")
                    if tx and isinstance(idx, int) and idx < len(chunk):
                        out.append(_tx_to_transfers(tx, wallet, chunk[idx]))
            return out
    except Exception as e:
        _log.warning("Helius txs fetch failed for %s: %s", wallet, e)
        return []


async def _birdeye_historical_price(
    mint: str,
    unix_time: int,
    cache: dict[_PriceKey, float | None],
) -> float | None:
    """Fetch USD price of `mint` near `unix_time`. Returns None on miss.

    Bucketed to hour so high-volume traders only spend one network call
    per (mint, hour) instead of one per swap.
    """
    if mint in STABLE_MINTS:
        return 1.0
    if not BIRDEYE_KEY:
        return None

    bucket = unix_time // 3600
    key = (mint, bucket)
    if key in cache:
        return cache[key]

    url = f"{BIRDEYE_BASE}/defi/history_price"
    # Birdeye `/history_price` is "address by_type=token" with a single
    # point. Use the 1H candle nearest the tx time — close enough for
    # cost-basis purposes.
    params = {
        "address": mint,
        "address_type": "token",
        "type": "1H",
        "time_from": unix_time - 3600,
        "time_to": unix_time + 3600,
    }
    headers = {"X-API-KEY": BIRDEYE_KEY, "x-chain": "solana"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            resp = await c.get(url, params=params, headers=headers)
            if resp.status_code != 200:
                cache[key] = None
                return None
            items = (resp.json().get("data") or {}).get("items") or []
            if not items:
                cache[key] = None
                return None
            # Pick the candle whose `unixTime` is closest to the tx slot.
            best = min(items, key=lambda c: abs(int(c.get("unixTime", 0)) - unix_time))
            price = float(best.get("value") or best.get("close") or 0)
            cache[key] = price if price > 0 else None
            return cache[key]
    except Exception:
        cache[key] = None
        return None


def _extract_deltas(tx: dict[str, Any], wallet: str) -> list[tuple[str, float, bool]]:
    """Pull every wallet-side token movement out of a Helius enhanced tx.

    Returns a list of `(mint, amount, is_buy)` tuples — one per token that
    crossed the wallet boundary in this transaction. `amount` is always a
    positive float in human units (already-decimals-adjusted by Helius).
    Native SOL transfers are folded in via the wrapped-SOL mint so the
    aggregator only sees SPL flows.

    SWAP txs surface up to 4 transfers (in/out for both legs); we count
    each as a separate delta — the caller still needs to look up the
    historical price per leg. Failed transactions are skipped at the call
    site (we never reach this function for them).
    """
    out: list[tuple[str, float, bool]] = []

    # SPL token transfers — Helius decodes `mint`, `tokenAmount` (decimal-
    # adjusted), `fromUserAccount`, `toUserAccount`.
    for tt in tx.get("tokenTransfers", []) or []:
        mint = tt.get("mint")
        amt = tt.get("tokenAmount")
        if not mint or amt is None:
            continue
        try:
            amount = float(amt)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        if tt.get("toUserAccount") == wallet:
            out.append((mint, amount, True))
        elif tt.get("fromUserAccount") == wallet:
            out.append((mint, amount, False))

    # Native SOL movements — Helius gives lamports (1e9 = 1 SOL).
    for nt in tx.get("nativeTransfers", []) or []:
        lamports = nt.get("amount")
        if lamports is None:
            continue
        try:
            sol = float(lamports) / 1e9
        except (TypeError, ValueError):
            continue
        # Filter dust SOL transfers (rent refunds, priority fees) — they
        # would otherwise show up as "bought 0.000005 SOL" and pollute
        # the cost basis. The 0.01 SOL floor catches all real movements.
        if sol < 0.01:
            continue
        if nt.get("toUserAccount") == wallet:
            out.append((SOL_MINT, sol, True))
        elif nt.get("fromUserAccount") == wallet:
            out.append((SOL_MINT, sol, False))

    return out


async def _apply_batch(
    session: AsyncSession,
    wallet: str,
    deltas: dict[str, dict[str, float]],
) -> None:
    """UPSERT one row per mint with the accumulated bought/sold totals."""
    if not deltas:
        return
    for mint, sums in deltas.items():
        await session.execute(
            text(
                """
                INSERT INTO chat_schema.wallet_token_costbasis
                    (wallet, mint,
                     total_bought_amount, total_bought_usd,
                     total_sold_amount,   total_sold_usd,
                     last_signature, last_processed_at, updated_at)
                VALUES
                    (:wallet, :mint,
                     :ba, :bu,
                     :sa, :su,
                     :sig, NOW(), NOW())
                ON CONFLICT (wallet, mint) DO UPDATE SET
                    total_bought_amount = chat_schema.wallet_token_costbasis.total_bought_amount + EXCLUDED.total_bought_amount,
                    total_bought_usd    = chat_schema.wallet_token_costbasis.total_bought_usd    + EXCLUDED.total_bought_usd,
                    total_sold_amount   = chat_schema.wallet_token_costbasis.total_sold_amount   + EXCLUDED.total_sold_amount,
                    total_sold_usd      = chat_schema.wallet_token_costbasis.total_sold_usd      + EXCLUDED.total_sold_usd,
                    last_signature      = EXCLUDED.last_signature,
                    last_processed_at   = NOW(),
                    updated_at          = NOW();
                """
            ),
            {
                "wallet": wallet,
                "mint": mint,
                "ba": sums["ba"],
                "bu": sums["bu"],
                "sa": sums["sa"],
                "su": sums["su"],
                "sig": sums.get("sig"),
            },
        )


async def _get_sync_state(session: AsyncSession, wallet: str) -> dict[str, Any]:
    row = await session.execute(
        text(
            "SELECT last_processed_signature, backfill_complete "
            "FROM chat_schema.wallet_costbasis_sync WHERE wallet = :w"
        ),
        {"w": wallet},
    )
    r = row.first()
    if r is None:
        return {"last_signature": None, "backfill_complete": False, "exists": False}
    return {"last_signature": r[0], "backfill_complete": r[1], "exists": True}


async def _set_sync_state(
    session: AsyncSession,
    wallet: str,
    *,
    last_signature: str | None,
    backfill_complete: bool,
    error: str | None = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO chat_schema.wallet_costbasis_sync
                (wallet, last_processed_signature, last_processed_at, backfill_complete, last_error)
            VALUES (:w, :sig, NOW(), :done, :err)
            ON CONFLICT (wallet) DO UPDATE SET
                last_processed_signature = EXCLUDED.last_processed_signature,
                last_processed_at        = NOW(),
                backfill_complete        = EXCLUDED.backfill_complete,
                error_count              = chat_schema.wallet_costbasis_sync.error_count
                                            + CASE WHEN EXCLUDED.last_error IS NOT NULL THEN 1 ELSE 0 END,
                last_error               = EXCLUDED.last_error;
            """
        ),
        {
            "w": wallet,
            "sig": last_signature,
            "done": backfill_complete,
            "err": error,
        },
    )


async def sync_wallet_costbasis(session: AsyncSession, wallet: str) -> dict[str, Any]:
    """Walk Helius enhanced txs for `wallet`, persist running cost basis.

    Returns a small summary (#pages, #txs, #unique mints, errors). The
    route layer logs this for observability. The frontend doesn't need it;
    it just calls `/portfolio/pnl/{wallet}` afterwards to read the freshly
    populated table.
    """
    if not HELIUS_KEY:
        return {"status": "no_helius_key", "txs": 0, "mints": 0}

    sync_state = await _get_sync_state(session, wallet)
    stop_at_sig: str | None = sync_state["last_signature"] if sync_state["exists"] else None
    incremental = stop_at_sig is not None

    aggregate: dict[str, dict[str, float]] = {}
    price_cache: dict[_PriceKey, float | None] = {}
    cursor: str | None = None
    pages = 0
    txs_seen = 0
    new_first_sig: str | None = None  # newest signature we've consumed this run

    max_pages = HELIUS_PAGE if incremental else INITIAL_BACKFILL_PAGES

    try:
        while pages < max_pages:
            page = await _helius_get_txs(wallet, before=cursor)
            if not page:
                break
            pages += 1

            for tx in page:
                sig = tx.get("signature")
                if sig and new_first_sig is None:
                    # Helius returns newest-first, so the very first
                    # signature we see across all pages is the new high
                    # watermark.
                    new_first_sig = sig
                if incremental and sig == stop_at_sig:
                    # Caught up to where we left off — bail out cleanly.
                    cursor = None  # sentinel to stop outer loop
                    break
                if tx.get("transactionError"):
                    continue
                txs_seen += 1
                ts = int(tx.get("timestamp", 0))
                if ts <= 0:
                    continue
                deltas = _extract_deltas(tx, wallet)
                if not deltas:
                    continue
                # Fan out price lookups across this tx's legs in parallel.
                prices = await asyncio.gather(
                    *(_birdeye_historical_price(m, ts, price_cache) for m, _, _ in deltas),
                    return_exceptions=False,
                )
                for (mint, amount, is_buy), price in zip(deltas, prices):
                    usd = (price or 0.0) * amount
                    bucket = aggregate.setdefault(
                        mint,
                        {"ba": 0.0, "bu": 0.0, "sa": 0.0, "su": 0.0, "sig": sig or ""},
                    )
                    if is_buy:
                        bucket["ba"] += amount
                        bucket["bu"] += usd
                    else:
                        bucket["sa"] += amount
                        bucket["su"] += usd

            if cursor is None and incremental:
                break
            cursor = page[-1].get("signature") if page else None
            if not cursor:
                break

        await _apply_batch(session, wallet, aggregate)
        await _set_sync_state(
            session,
            wallet,
            last_signature=new_first_sig or stop_at_sig,
            # Backfill is "complete" once we've either caught up
            # incrementally or exhausted the initial-backfill window.
            backfill_complete=True,
        )
        return {
            "status": "ok",
            "pages": pages,
            "txs": txs_seen,
            "mints": len(aggregate),
            "incremental": incremental,
        }
    except Exception as e:
        _log.exception("cost-basis sync failed for %s", wallet)
        await _set_sync_state(
            session,
            wallet,
            last_signature=stop_at_sig,
            backfill_complete=sync_state.get("backfill_complete", False),
            error=str(e)[:500],
        )
        return {"status": "error", "error": str(e), "pages": pages, "txs": txs_seen}


async def read_costbasis(session: AsyncSession, wallet: str) -> list[dict[str, Any]]:
    """Read the persisted cost-basis table for `wallet`.

    The frontend joins this against its current balances + current prices
    to compute realized/unrealized/total PnL per holding — the math sits
    client-side because current prices change every second and we don't
    want to bake them into a cached server-side response.
    """
    result = await session.execute(
        text(
            """
            SELECT
                mint,
                total_bought_amount, total_bought_usd,
                total_sold_amount,   total_sold_usd,
                last_processed_at
            FROM chat_schema.wallet_token_costbasis
            WHERE wallet = :w
            """
        ),
        {"w": wallet},
    )
    rows = result.fetchall()
    return [
        {
            "mint": r[0],
            "totalBoughtAmount": float(r[1] or 0),
            "totalBoughtUsd": float(r[2] or 0),
            "totalSoldAmount": float(r[3] or 0),
            "totalSoldUsd": float(r[4] or 0),
            "lastProcessedAt": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]
