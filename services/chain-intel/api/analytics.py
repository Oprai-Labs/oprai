"""The flexible query layer — any question, expressed as SQL over the CLEAN tables,
run behind a gate that keeps it correct, fast and safe:

  * allowlist: only the modelled tables/views (trades, launches, token_state_v …) —
    never the raw billions (logs, token_transfers, transactions); semantics such as
    swapper-vs-pool deltas, decimals, quote side and the real actor are already
    resolved inside these tables, so a query cannot get them wrong.
  * read-only, one statement, no table functions, LIMIT enforced (default 200,
    max 2000), a hard time limit (default 3 s, max 20 s).
  * cost gate: EXPLAIN ESTIMATE before running — a query that would read more than
    the budget is refused with the estimate and a hint (narrow the window / add a
    filter), or accepted as an async job when the caller asks for it.
  * every answer carries the SQL that ran, the rows read and the elapsed ms, so the
    number can be audited.

SCHEMA below is the contract given to the LLM (with metric definitions), so the
same concept — "graduated", "hit 100k", "runner 10x" — always maps to the same SQL."""
from __future__ import annotations

import asyncio
import re
import time
import uuid

import httpx

from . import ch
from .ch import CH_URL, CH_USER, CH_PASS, CH_DB

ALLOWED_TABLES = {
    "trades", "launches", "token_stats", "token_state_v", "wallet_state_v", "token_hour_v", "wallet_hour_v",
    "token_supply", "token_decimals", "smart_wallets", "wallet_token_positions", "wallet_metrics",
    "token_metrics", "dex_pools", "v4_pool_keys", "pons_curves", "protocol_registry", "locker_positions",
    "weth_price", "token_price_daily", "dex_swaps", "launch_creators", "contracts", "blocks",
}
FORBIDDEN = re.compile(r"\b(INSERT|ALTER|DROP|CREATE|SYSTEM|TRUNCATE|OPTIMIZE|KILL|ATTACH|DETACH|RENAME|GRANT|REVOKE|SET|USE|"
                       r"url|file|s3|remote|remoteSecure|input|mysql|postgresql|jdbc|odbc|hdfs|azureBlobStorage|executable|"
                       r"cluster|clusterAllReplicas|view|merge|numbers|generateRandom|dictionary)\s*\(?", re.I)
TABLE_REF = re.compile(r"\b(?:FROM|JOIN|IN)\s+\(?\s*(?:rh\.)?([A-Za-z_][A-Za-z0-9_]*)", re.I)
MAX_ROWS_TO_READ = 60_000_000       # cost budget for a synchronous query (~1-2 s)
DEFAULT_LIMIT, MAX_LIMIT = 200, 2000
DEFAULT_TIMEOUT, MAX_TIMEOUT = 3.0, 20.0
_jobs: dict[str, dict] = {}

SCHEMA = {
    "notes": [
        "All addresses are lowercase 0x strings. Quote assets: ETH = 0x0000000000000000000000000000000000000000, "
        "WETH = 0x0bd7d308f8e1639fab988df18a8011f41eacad73, USDG = 0x5fc5360d0400a0fd4f2af552add042d716f1d168 (6 decimals), "
        "USDe = 0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34.",
        "Amounts in trades/token_state_v are HUMAN units (decimals applied). usd = quote-side USD at the trade's time; "
        "usd = 0 means the quote could not be priced — filter usd > 0 for money questions.",
        "Chain runs ~10 blocks/s. Times are UTC DateTime. Use now() - INTERVAL n DAY/HOUR for windows.",
        "Prefer token_stats (materialized, refreshed every few minutes, ms) for anything per token — dev/launchpad cohorts, ATH, mcap, "
        "drawdown, 24h/7d volume. token_state_v is the live view (slower, ~1 s when not filtered by token). wallet_state_v / *_hour_v for wallets and series.",
        "Full-table aggregates over trades (145M rows) are refused as heavy — use token_stats / wallet_state_v / hourly views, or add a token/actor/time filter.",
        "Always alias aggregates with names that differ from column names (ClickHouse resolves aliases first).",
    ],
    "tables": {
        "trades": {
            "grain": "one row per swap leg touching a non-quote token; keyed by the real actor (ERC-4337 account behind a bundler when relayed)",
            "columns": {
                "ts": "DateTime", "block": "UInt64", "tx_hash": "String", "log_index": "UInt32",
                "actor": "wallet that made the trade", "relayed": "1 if via bundler/relayer",
                "side": "'buy' (actor received token) | 'sell'", "token": "the non-quote token",
                "token_amount": "human units", "quote": "quote token address (ETH/WETH/USDG/USDe or another token)",
                "quote_amount": "human units", "usd": "USD value of the leg", "price_usd": "usd / token_amount",
                "venue": "'uniswap-v4' | 'uniswap-v3' | 'uniswap-v2' | 'pons-curve'", "pool": "pool address / V4 poolId / curve",
                "hook": "V4 hook address ('' if none) — identifies the launchpad, see launches.launchpad",
            },
            "fast_filters": ["actor = …", "token = … (projection)", "ts BETWEEN …"],
        },
        "launches": {
            "grain": "one row per launched token",
            "columns": {
                "token": "String", "dev": "creator wallet = the launch tx sender (Pons, Doppler, Clanker… all indexed) or the contract deployer; '' only when neither is known",
                "dev_source": "'launch event' | 'contract deployer' | ''",
                "launchpad": "Pons | Doppler (Bankr / LONG / Zora launches) | Clanker | letscash.fun | Klik | Flaunch | o1 Launchpad | PAIR (pair.fund) | Pons V2 | lunch.fun | Livo | pmav.fun | Bags | Bow | StonkBroker | Noxa | LONG | direct pool | unknown",
                "launchpad_source": "factory | uniswap-v4 hook | creation router | first pool",
                "launch_ts": "DateTime", "launch_block": "UInt64", "curve": "Pons curve contract or ''", "hook": "V4 hook or ''",
                "first_pool": "String", "graduated": "1 if the token got an open (non-curve / non-launch-hook) pool after launch = migrated",
                "graduated_ts": "DateTime (1970 if not)",
            },
        },
        "token_stats": {
            "grain": "one row per token, materialized every few minutes — THE table for token questions and cohorts",
            "columns": {
                "token": "", "symbol": "", "name": "", "trades": "", "buys": "", "sells": "", "vol_usd": "", "buy_usd": "", "sell_usd": "",
                "traders": "", "buyers": "", "first_trade_ts": "", "last_trade_ts": "",
                "price_usd": "latest hourly VWAP", "first_price_usd": "first hour's VWAP", "ath_price_usd": "max hourly VWAP (hours ≥ $20)", "ath_ts": "",
                "supply": "", "mcap_usd": "", "ath_mcap_usd": "", "first_mcap_usd": "", "drawdown": "1 − price/ath", "ath_multiple": "ath / first price",
                "vol_24h_usd": "", "trades_24h": "", "buyers_24h": "", "vol_7d_usd": "",
                "holders": "", "dev": "creator ('' if unknown)", "launchpad": "", "launch_ts": "", "graduated": "", "graduated_ts": "", "venues": "", "updated_at": "",
            },
            "fast_filters": ["token = …", "dev = … (bloom index)", "launchpad = …", "launch_ts > …"],
        },
        "token_state_v": {
            "grain": "one row per token (all-time, live view; prefer token_stats)",
            "columns": {
                "token": "", "trades": "", "buys": "", "sells": "", "vol_usd": "", "buy_usd": "", "sell_usd": "",
                "traders": "distinct actors", "buyers": "distinct buyers", "first_trade_ts": "", "last_trade_ts": "",
                "ath_price_usd": "max trade price (trades ≥ $5)", "ath_ts": "", "first_price_usd": "", "price_usd": "last trade price",
                "venues": "Array(String)", "supply": "minted − burned (human)", "mcap_usd": "price × supply",
                "ath_mcap_usd": "ath price × supply", "drawdown": "1 − price/ath (0..1)",
                "dev": "", "launchpad": "", "launch_ts": "", "graduated": "", "graduated_ts": "", "holders": "",
            },
        },
        "wallet_state_v": {
            "grain": "one row per wallet (all-time trading, live)",
            "columns": {"wallet": "", "trades": "", "buys": "", "sells": "", "vol_usd": "", "buy_usd": "", "sell_usd": "",
                        "tokens": "distinct tokens traded", "first_trade_ts": "", "last_trade_ts": "", "venues": "Array", "relayed_trades": ""},
        },
        "token_hour_v": {"grain": "token × hour", "columns": {"token": "", "hour": "", "trades": "", "buys": "", "sells": "", "vol_usd": "",
                                                            "buy_usd": "", "sell_usd": "", "traders": "", "buyers": "", "high": "", "low": "", "open": "", "close": ""}},
        "wallet_hour_v": {"grain": "wallet × hour", "columns": {"wallet": "", "hour": "", "trades": "", "buys": "", "sells": "", "vol_usd": "", "buy_usd": "", "sell_usd": "", "tokens": ""}},
        "wallet_token_positions": {"grain": "wallet × token FIFO P&L (refreshed every 4h)",
                                   "columns": {"wallet": "", "token": "", "qty_in": "", "usd_in": "", "qty_out": "", "usd_out": "", "avg_cost": "",
                                               "realized_pnl": "", "holding": "", "first_buy_ts": "", "last_ts": ""}},
        "wallet_metrics": {"grain": "wallet (refreshed every 4h)", "columns": {"wallet": "", "realized_pnl": "", "unrealized_pnl": "", "roi": "", "win_rate": "",
                                                                            "n_tokens": "", "trade_count": "", "avg_hold_h": "", "active_days": "", "first_seen": "", "last_seen": "", "smart_score": ""}},
        "smart_wallets": {"grain": "top-5000 smart EOAs (rolling)", "columns": {"wallet": "", "rank": "", "smart_score": "", "score_rolling": "", "realized_pnl": "",
                                                                             "win_rate": "", "n_tokens": "", "pnl_30d": "", "win_rate_30d": "", "n_tokens_30d": "", "last_active": ""}},
        "token_metrics": {"grain": "token (refreshed every 4h)", "columns": {"token": "", "holders": "", "transfers": "", "volume_usd": "", "first_block": "", "sniper_count": "", "bundle_count": "", "smart_holders": "", "top10_pct": ""}},
        "dex_pools": {"grain": "pool", "columns": {"pool": "", "token0": "", "token1": "", "dex": "", "created_block": "", "fee": "", "hooks": ""}},
        "v4_pool_keys": {"grain": "V4 pool", "columns": {"pool": "", "token0": "", "token1": "", "fee": "", "tick_spacing": "", "hooks": "", "created_block": ""}},
        "pons_curves": {"grain": "Pons curve", "columns": {"curve": "", "token": "", "quote": "", "created_block": ""}},
        "contracts": {"grain": "contract", "columns": {"address": "", "deployer": "", "creation_block": "", "is_token": "", "symbol": "", "name": ""}},
        "blocks": {"grain": "block", "columns": {"number": "", "timestamp": ""}},
    },
    "metrics": {
        "graduated": "launches.graduated = 1",
        "hit_100k": "token_stats.ath_mcap_usd >= 100000",
        "hit_1m": "token_stats.ath_mcap_usd >= 1000000",
        "runner_10x": "token_stats.ath_multiple >= 10",
        "rugged": "token_stats.drawdown >= 0.95 AND last_trade_ts < now() - INTERVAL 3 DAY",
        "alive": "token_stats.last_trade_ts >= now() - INTERVAL 1 DAY",
        "dev_hit_rate": "countIf(ath_mcap_usd >= 100000) / count() over a dev's tokens in token_stats",
        "wallet_pnl_window": "sum(realized_pnl) from wallet_token_positions WHERE last_ts > now() - INTERVAL n DAY",
        "entry_mcap": "join trades (side='buy') to token_state_v: price_usd * supply at the buy",
        "smart_inflow_24h": "sum(usd) from trades t JOIN smart_wallets s ON s.wallet = t.actor WHERE side='buy' AND ts > now() - INTERVAL 1 DAY",
    },
    "examples": [
        {"q": "this dev's tokens by launchpad with graduation and hit rates",
         "sql": "SELECT launchpad, count() AS n, countIf(graduated) AS graduated_n, countIf(ath_mcap_usd >= 100000) AS hit_100k, countIf(ath_mcap_usd >= 1e6) AS hit_1m, round(median(ath_mcap_usd)) AS median_ath FROM token_stats WHERE dev = '0x…' GROUP BY launchpad ORDER BY n DESC"},
        {"q": "smart wallets' buys in the last hour, by token",
         "sql": "SELECT t.token, count() AS buys, uniq(t.actor) AS smart_buyers, round(sum(t.usd)) AS usd FROM trades t INNER JOIN smart_wallets s ON s.wallet = t.actor WHERE t.side = 'buy' AND t.ts > now() - INTERVAL 1 HOUR GROUP BY t.token ORDER BY smart_buyers DESC LIMIT 20"},
        {"q": "Doppler launches of the last 7 days that fell 70%+ from ATH",
         "sql": "SELECT token, symbol, launch_ts, round(ath_mcap_usd) AS ath, round(mcap_usd) AS mcap, round(drawdown, 2) AS dd FROM token_stats WHERE launchpad LIKE 'Doppler%' AND launch_ts > now() - INTERVAL 7 DAY AND drawdown >= 0.7 ORDER BY ath DESC LIMIT 50"},
    ],
}


def validate(sql: str, limit: int) -> str:
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise ValueError("one statement only")
    if not re.match(r"^\s*(SELECT|WITH)\b", s, re.I):
        raise ValueError("only SELECT (or WITH … SELECT) is allowed")
    if FORBIDDEN.search(s):
        m = FORBIDDEN.search(s)
        raise ValueError(f"not allowed: {m.group(0).strip()}")
    ctes = set(re.findall(r"(?:WITH|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(", s, re.I))
    for t in TABLE_REF.findall(s):
        if t.lower() in ("select",) or t in ctes:
            continue
        if t not in ALLOWED_TABLES:
            raise ValueError(f"table not exposed: {t} (allowed: {', '.join(sorted(ALLOWED_TABLES))})")
    if not re.search(r"\bLIMIT\s+\d+", s, re.I):
        s += f"\nLIMIT {limit}"
    else:
        s = re.sub(r"\bLIMIT\s+(\d+)", lambda m: f"LIMIT {min(int(m.group(1)), MAX_LIMIT)}", s, flags=re.I)
    return s


async def estimate(sql: str) -> int:
    try:
        rows = await ch.q(f"EXPLAIN ESTIMATE {sql}", timeout=5)
        return int(sum(int(r.get("rows") or 0) for r in rows))
    except Exception:
        return -1


async def _run(sql: str, timeout: float) -> dict:
    t = time.time()
    async with httpx.AsyncClient(timeout=timeout + 2) as c:
        r = await c.post(CH_URL, params={"user": CH_USER, "password": CH_PASS, "database": CH_DB, "readonly": 1,
                                          "max_execution_time": timeout, "max_result_rows": MAX_LIMIT,
                                          "max_memory_usage": 4_000_000_000, "default_format": "JSONCompact"},
                         content=sql.encode())
    if r.status_code != 200:
        txt = r.text.strip()
        m = re.search(r"DB::Exception: (.*?)(?: \(version|$)", txt, re.S)
        msg = (m.group(1) if m else txt.split("\n")[0])[:400]
        raise ValueError(f"query failed: {msg}")
    j = r.json()
    return {"columns": [m["name"] for m in j["meta"]], "rows": j["data"], "row_count": len(j["data"]),
            "rows_read": (j.get("statistics") or {}).get("rows_read"), "ms": int((time.time() - t) * 1000)}


async def query(sql: str, limit: int = DEFAULT_LIMIT, timeout: float = DEFAULT_TIMEOUT, allow_async: bool = False) -> dict:
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    timeout = max(0.5, min(float(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    s = validate(sql, limit)
    est = await estimate(s)
    if est > MAX_ROWS_TO_READ:
        if not allow_async:
            return {"ok": False, "heavy": True, "estimate_rows": est, "sql": s,
                    "hint": "this would read %.0fM rows; narrow the time window / add a token, wallet or launchpad filter, "
                            "or re-run with allow_async=true to get a job id" % (est / 1e6)}
        job = uuid.uuid4().hex[:12]
        _jobs[job] = {"status": "running", "sql": s, "started": time.time()}

        async def run_job():
            try:
                _jobs[job].update(await _run(s, MAX_TIMEOUT * 6), status="done")
            except Exception as e:
                _jobs[job].update(status="failed", error=str(e)[:300])
        asyncio.create_task(run_job())
        return {"ok": True, "job": job, "estimate_rows": est, "sql": s}
    res = await _run(s, timeout)
    return {"ok": True, "sql": s, "estimate_rows": est, **res}


def job(job_id: str) -> dict:
    return _jobs.get(job_id) or {"status": "unknown"}
