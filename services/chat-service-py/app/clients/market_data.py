"""
Market data client — aggregates Birdeye, DexScreener, Helius, Jupiter data APIs.

These functions are called directly by chat-service-py when the LLM invokes
a data-query action type (birdeye_price, dex_token, helius_wallet_txs, etc.).
Results are formatted as text and streamed inline in chat — no action card.

API keys: BIRDEYE_API_KEY, HELIUS_API_KEY (from environment).
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from prometheus_client import Counter, Histogram

_log = logging.getLogger(__name__)

# Base58 alphabet (Bitcoin/Solana): no 0, O, I, l. Used to sanity-check that
# an SNS resolve result is a real pubkey and not a sentinel string like
# "Domain not found".
_BASE58_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]+")

# ── Per-provider Prometheus metrics ───────────────────────────────────────────
# Provider is the URL host bucketed to a stable label so Grafana dashboards
# don't explode on per-tenant subdomains. Status: ok | http_4xx | http_5xx |
# timeout | network_error. Latency is a histogram so we can p99 / p50 each
# provider independently and alert on regressions per-source.
EXTERNAL_API_REQUESTS = Counter(
    "external_api_requests_total",
    "Outbound calls to external market-data providers",
    ["provider", "status"],
)
EXTERNAL_API_LATENCY = Histogram(
    "external_api_request_duration_seconds",
    "Outbound external API request latency",
    ["provider"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0),
)


def _provider_label(url: str) -> str:
    """Bucket a URL into a provider label.

    `https://public-api.birdeye.so/defi/price` → `birdeye`. Keeps the metric
    cardinality bounded — one row per provider, not per endpoint.
    """
    try:
        host = urlparse(url).hostname or "unknown"
    except Exception:
        return "unknown"
    if "birdeye" in host:
        return "birdeye"
    if "helius" in host:
        return "helius"
    if "dexscreener" in host:
        return "dexscreener"
    if "jup.ag" in host or "jupiter" in host:
        return "jupiter"
    if "kamino" in host:
        return "kamino"
    if "raydium" in host:
        return "raydium"
    if "magiceden" in host:
        return "magic_eden"
    if "relay.link" in host:
        return "relay"
    if "solana.com" in host:
        return "solana_rpc"
    return host.replace(".", "_")[:32]


def _classify_status(exc: Exception | None, response: httpx.Response | None) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if exc is not None:
        return "network_error"
    if response is None:
        return "unknown"
    code = response.status_code
    if 200 <= code < 400:
        return "ok"
    if 400 <= code < 500:
        return "http_4xx"
    return "http_5xx"

BIRDEYE_BASE  = "https://public-api.birdeye.so"
BIRDEYE_KEY   = os.environ.get("BIRDEYE_API_KEY", "")
HELIUS_RPC    = f"https://mainnet.helius-rpc.com/?api-key={os.environ.get('HELIUS_API_KEY', '')}"
HELIUS_API    = "https://api.helius.xyz/v0"
HELIUS_KEY    = os.environ.get("HELIUS_API_KEY", "")
SOLANA_RPC    = os.environ.get("SOLANA_RPC", "https://api.mainnet-beta.solana.com")
GATEWAY_URL   = os.environ.get("GATEWAY_URL", "http://localhost:3001")
# Direct address of the Rust solana-service. The Python read-only query path
# (`_solana_action_data`) talks to it server-to-server, bypassing the gateway:
# the gateway's CSRF + JWT layer is for browser users, and adding those
# headers from a backend service is wrong (no real CSRF risk on internal
# calls, no JWT to forward — chat-service authenticates with the shared
# `x-internal-api-key` instead).
# Server-side /actions/build reads must hit the Solana service DIRECTLY, not the
# gateway: the gateway's /actions/* is browser-facing (CSRF X-Requested-With +
# user JWT), so an internal service-to-service call there is rejected 403/401 —
# which surfaced as "couldn't reach Raydium data, try again". The compose sets
# SOLANA_SERVICE_HTTP to the direct address (solana-service-rs:3030); prefer it,
# then any explicit SOLANA_SERVICE_URL, then the local default.
SOLANA_SERVICE_URL = (
    os.environ.get("SOLANA_SERVICE_HTTP")
    or os.environ.get("SOLANA_SERVICE_URL")
    or "http://localhost:3030"
)
INTERNAL_KEY  = os.environ.get("OPRAI_INTERNAL_API_KEY", "")
TIMEOUT       = 12.0
_MAX_CHARS    = 16_000  # raised from 6_000 so server-ranked wallet PnL payload (~7.5 KB) and token deep-dive composite (multiple ranked arrays) fit; LLM-side cap in message.py was also bumped to 16_000 to match
_VALIDATOR_MAX_COMMISSION = 10  # % — exclude validators above this threshold
_VALIDATOR_BASE_APY       = 7.0  # % — approximate Solana network inflation yield


# ── Helpers ───────────────────────────────────────────────────────────────────

def _birdeye_headers(chain: str = "solana") -> dict:
    if not BIRDEYE_KEY:
        raise ValueError("BIRDEYE_API_KEY not configured")
    return {"X-API-KEY": BIRDEYE_KEY, "x-chain": chain}


async def _get(url: str, params: dict = None, headers: dict = None) -> Any:
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    provider = _provider_label(url)
    started = time.perf_counter()
    response: httpx.Response | None = None
    err: Exception | None = None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            response = await c.get(url, params=clean, headers=headers or {})
            response.raise_for_status()
            return response.json()
    except Exception as e:
        err = e
        raise
    finally:
        EXTERNAL_API_LATENCY.labels(provider=provider).observe(time.perf_counter() - started)
        EXTERNAL_API_REQUESTS.labels(provider=provider, status=_classify_status(err, response)).inc()


async def _post(url: str, body: dict, headers: dict = None) -> Any:
    provider = _provider_label(url)
    started = time.perf_counter()
    response: httpx.Response | None = None
    err: Exception | None = None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            response = await c.post(url, json=body, headers=headers or {})
            response.raise_for_status()
            return response.json()
    except Exception as e:
        err = e
        raise
    finally:
        EXTERNAL_API_LATENCY.labels(provider=provider).observe(time.perf_counter() - started)
        EXTERNAL_API_REQUESTS.labels(provider=provider, status=_classify_status(err, response)).inc()


def _cap(data: Any, max_chars: int = _MAX_CHARS) -> Any:
    """Truncate large responses so they fit in LLM context."""
    if len(json.dumps(data, default=str)) <= max_chars:
        return data
    if isinstance(data, list):
        lo, hi = 0, len(data)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(json.dumps(data[:mid], default=str)) <= max_chars:
                lo = mid
            else:
                hi = mid - 1
        return data[:max(1, lo)]
    if isinstance(data, dict):
        truncated = {}
        for k, v in data.items():
            truncated[k] = _cap(v, max_chars) if isinstance(v, (list, dict)) else v
        if len(json.dumps(truncated, default=str)) <= max_chars:
            return truncated
        items = list(data.items())
        lo, hi = 0, len(items)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(json.dumps(dict(items[:mid]), default=str)) <= max_chars:
                lo = mid
            else:
                hi = mid - 1
        return dict(items[:max(1, lo)])
    return data


# ── Birdeye ───────────────────────────────────────────────────────────────────

async def birdeye_price(address: str, check_liquidity: bool = False) -> dict:
    """Real-time price for a single token."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/price",
        params={"address": address, "check_liquidity": check_liquidity},
        headers=_birdeye_headers(),
    )


async def birdeye_multi_price(list_address: str, chain: str = "solana") -> dict:
    """Real-time prices for multiple tokens (comma-separated mint addresses)."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/multi_price",
        params={"list_address": list_address},
        headers=_birdeye_headers(chain),
    )


async def birdeye_token_overview(address: str, chain: str = "solana") -> dict:
    """Comprehensive token overview — price, volume, liquidity, market stats."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/token_overview",
        params={"address": address},
        headers=_birdeye_headers(chain),
    )


async def birdeye_token_metadata(address: str, chain: str = "solana") -> dict:
    """Token metadata — name, symbol, decimals, logo."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/meta-data/single",
        params={"address": address},
        headers=_birdeye_headers(chain),
    )


async def birdeye_token_security(address: str, chain: str = "solana") -> dict:
    """Token security audit — mint authority, freeze authority, top holder concentration."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/token_security",
        params={"address": address},
        headers=_birdeye_headers(chain),
    )


async def birdeye_ohlcv(
    address: str,
    type: str = "1H",
    time_from: int = None,
    time_to: int = None,
    chain: str = "solana",
) -> dict:
    """OHLCV price chart data for a token. type: 1m|5m|15m|1H|4H|1D|1W."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/ohlcv",
        params={"address": address, "type": type, "time_from": time_from, "time_to": time_to},
        headers=_birdeye_headers(chain),
    )


async def birdeye_token_trending(
    interval: str = "24h",
    limit: int = 20,
    chain: str = "solana",
) -> dict:
    """Trending tokens — sorted by rank. interval: 1h|4h|24h."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/token_trending",
        params={"sort_by": "rank", "interval": interval, "sort_type": "asc", "limit": limit},
        headers=_birdeye_headers(chain),
    )


async def birdeye_new_listings(
    limit: int = 20,
    chain: str = "solana",
) -> dict:
    """Newly listed tokens on Birdeye."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/v2/tokens/new_listing",
        params={"limit": limit},
        headers=_birdeye_headers(chain),
    )


async def birdeye_token_holders(address: str, limit: int = 50) -> dict:
    """Top token holders — address, balance, % of supply."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/holder",
        params={"address": address, "limit": limit},
        headers=_birdeye_headers(),
    )


async def birdeye_wallet_portfolio(wallet: str, chain: str = "solana") -> dict:
    """Wallet token portfolio — all holdings with USD values."""
    return await _get(
        f"{BIRDEYE_BASE}/wallet/v2/current-net-worth",
        params={"wallet": wallet, "limit": 50},
        headers=_birdeye_headers(chain),
    )


async def birdeye_wallet_pnl(wallet: str, duration: str = "7d", chain: str = "solana") -> dict:
    """Wallet overall PnL — realized, unrealized, total return. duration: 24h|7d|30d|90d|all."""
    return await _get(
        f"{BIRDEYE_BASE}/wallet/v2/pnl/summary",
        params={"wallet": wallet, "duration": duration},
        headers=_birdeye_headers(chain),
    )


async def birdeye_wallet_pnl_details(
    wallet: str,
    duration: str = "all",
    sort_by: str = "last_trade",
    sort_type: str = "desc",
    limit: int = 50,
    chain: str = "solana",
) -> dict:
    """Per-token PnL breakdown for a wallet — returns the aggregate `summary`
    plus a pre-ranked `tokens` array containing the top 10 winners and top 10
    losers (sorted by total_usd PnL), trimmed so the LLM payload stays small
    enough to fit through the runtime tool-result cap.

    Why we rank server-side: Birdeye returns tokens by last_trade_unix_time,
    not PnL. The runtime truncates tool results to ~4 KB, so without ranking
    the LLM only ever sees the most-recently-traded tokens (often near-zero
    PnL) and never the actual top performers. By ranking here we guarantee
    the winners/losers reach the model regardless of cap.

    duration: 24h | 7d | 30d | 90d | all (default "all" — widest window)
    """
    # Birdeye API only accepts sort_by="last_trade"; ignore caller's sort_by/sort_type.
    body = {
        "wallet": wallet,
        "duration": duration,
        "sort_by": "last_trade",
        "sort_type": "desc",
        "limit": max(1, min(int(limit), 50)),
    }
    raw = await _post(
        f"{BIRDEYE_BASE}/wallet/v2/pnl/details",
        body=body,
        headers=_birdeye_headers(chain),
    )

    # Re-rank + compact server-side so the LLM payload stays well under the
    # ~4 KB tool-result cap. Without this, the full 30-token Birdeye response
    # is ~34 KB and gets truncated, leaving the model with only the first few
    # (most recently traded, not most profitable) tokens.
    if isinstance(raw, dict) and isinstance(raw.get("data"), dict):
        data = raw["data"]
        tokens = data.get("tokens") or []
        if isinstance(tokens, list) and tokens:
            def _total(t: dict) -> float:
                p = (t.get("pnl") or {})
                v = p.get("total_usd")
                return float(v) if v is not None else 0.0

            def _compact(t: dict) -> dict:
                """Keep only fields the LLM needs to write the report row."""
                p   = t.get("pnl") or {}
                c   = t.get("counts") or {}
                q   = t.get("quantity") or {}
                cf  = t.get("cashflow_usd") or {}
                pr  = t.get("pricing") or {}
                return {
                    "symbol":         t.get("symbol"),
                    "address":        t.get("address"),
                    "total_pnl":      p.get("total_usd"),
                    "realized":       p.get("realized_profit_usd"),
                    "unrealized":     p.get("unrealized_usd"),
                    "return_pct":     p.get("total_percent"),
                    "trades":         c.get("total_trade"),
                    "holding":        q.get("holding"),
                    "invested":       cf.get("total_invested"),
                    "current_value":  cf.get("current_value"),
                    "avg_buy_price":  pr.get("avg_buy_cost"),
                    "current_price":  pr.get("current_price"),
                }

            sorted_desc = sorted(tokens, key=_total, reverse=True)
            sorted_asc  = sorted(tokens, key=_total)

            winners = [_compact(t) for t in sorted_desc if _total(t) > 0][:10]
            losers  = [_compact(t) for t in sorted_asc  if _total(t) < 0][:10]

            data["tokens_total_count"]       = len(tokens)
            data["top_winners_by_total_pnl"] = winners
            data["top_losers_by_total_pnl"]  = losers
            # Drop the raw `tokens` array entirely — winners + losers cover
            # everything the model needs and removes ~25 KB of duplication.
            data.pop("tokens", None)
            data.pop("meta", None)  # rarely useful for analysis, saves space

    return raw


async def birdeye_wallet_first_funded(wallets: str, token_address: str | None = None, chain: str = "solana") -> dict:
    """First on-chain funding event for one or more wallets — reveals wallet age
    and where the SOL/USDC came from (CEX hot wallet, another personal wallet,
    bridge, airdrop). Critical for Nansen-style wallet provenance.

    wallets: comma-separated list (the underlying API expects a JSON array, we split here)
    token_address: optional — first time the wallet received THIS token, not first funded overall
    """
    wallet_list = [w.strip() for w in wallets.split(",") if w.strip()]
    body: dict = {"wallets": wallet_list}
    if token_address:
        body["token_address"] = token_address
    return await _post(
        f"{BIRDEYE_BASE}/wallet/v2/tx/first-funded",
        body=body,
        headers=_birdeye_headers(chain),
    )


async def birdeye_wallet_net_worth_history(
    wallet: str,
    count: int = 30,
    direction: str = "back",
    type: str = "1d",
    chain: str = "solana",
) -> dict:
    """Wallet net worth trajectory over time — USD snapshots, reveals growth
    pattern and drawdowns.
    type: 1d (daily) | 1h (hourly)
    count: how many points (default 30)
    direction: back (going back from now) | forward
    """
    return await _get(
        f"{BIRDEYE_BASE}/wallet/v2/net-worth",
        params={
            "wallet": wallet, "count": count,
            "direction": direction, "type": type, "sort_type": "desc",
        },
        headers=_birdeye_headers(chain),
    )


async def birdeye_holder_distribution(
    token_address: str,
    mode: str = "top",
    top_n: int = 10,
    min_percent: float | None = None,
    max_percent: float | None = None,
    chain: str = "solana",
) -> dict:
    """Token holder concentration analytics.
    mode='top'     → top N holders + their cumulative %
    mode='percent' → wallets holding between min_percent and max_percent of supply
    Use this to answer "how concentrated is X" / "is the top 10 over 30%".
    """
    params: dict = {
        "token_address": token_address, "address_type": "wallet",
        "mode": mode, "include_list": True, "limit": 50,
    }
    if mode == "top":
        params["top_n"] = top_n
    else:
        if min_percent is not None: params["min_percent"] = min_percent
        if max_percent is not None: params["max_percent"] = max_percent
    return await _get(
        f"{BIRDEYE_BASE}/holder/v1/distribution",
        params=params,
        headers=_birdeye_headers(chain),
    )


async def birdeye_holder_profile(token_address: str, interval: str = "1h", chain: str = "solana") -> dict:
    """Token holder growth profile — total holders, new (joined), churned (left),
    volume by holder cohort. Use this to see if a token is gaining or losing
    holders over the chosen interval.
    interval: 1h | 4h | 24h
    """
    return await _get(
        f"{BIRDEYE_BASE}/token/v1/holder-profile",
        params={
            "token_address": token_address, "interval": interval,
            "ui_amount_mode": "scaled", "include_zero_balance": True,
        },
        headers=_birdeye_headers(chain),
    )


async def birdeye_token_trade_data(address: str, chain: str = "solana") -> dict:
    """Real-time trade flow for a token — buy/sell counts, B/S ratio, unique
    traders, volume by side. Critical for accumulation-vs-distribution reads."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/trade-data/single",
        params={"address": address},
        headers=_birdeye_headers(chain),
    )


async def birdeye_holder_positions(token_address: str, labels: str | None = "bundler,sniper,insider", chain: str = "solana") -> dict:
    """Holders matching specific behavioural labels — sniper (bought at launch),
    bundler (multi-wallet coordinated buy), insider (deployer-linked), dev.
    Critical signal for memecoin/new-launch risk assessment.

    labels: comma-separated subset of: bundler | sniper | insider | dev (None = all holders)
    """
    return await _get(
        f"{BIRDEYE_BASE}/token/v1/holder-positions",
        params={
            "token_address": token_address, "labels": labels,
            "sort_by": "amount", "order_type": "desc",
            "include_zero_balance": True, "limit": 50,
        },
        headers=_birdeye_headers(chain),
    )


async def birdeye_smart_money(interval: str = "1d", limit: int = 20) -> dict:
    """Tokens being accumulated by smart money wallets. interval: 1d|7d|30d."""
    return await _get(
        f"{BIRDEYE_BASE}/smart-money/v1/token/list",
        params={"interval": interval, "sort_by": "smart_traders_no", "sort_type": "desc", "limit": limit},
        headers=_birdeye_headers(),
    )


async def birdeye_token_top_traders(
    address: str,
    time_frame: str = "24h",
    limit: int = 10,
) -> dict:
    """Top traders for a token — ranked by volume, PnL."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/v2/tokens/top_traders",
        params={"address": address, "time_frame": time_frame, "sort_by": "volume", "limit": limit},
        headers=_birdeye_headers(),
    )


async def birdeye_search(keyword: str, limit: int = 20, chain: str = "solana") -> dict:
    """Search tokens and markets by name, symbol, or address."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/search",
        params={"keyword": keyword, "chain": chain, "limit": limit, "sort_by": "liquidity"},
        headers=_birdeye_headers(chain),
    )


async def birdeye_price_history(
    address: str,
    type: str = "1D",
    chain: str = "solana",
) -> dict:
    """Historical price data for a token. type: 1m|5m|15m|1H|4H|1D|1W."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/history_price",
        params={"address": address, "address_type": "token", "type": type},
        headers=_birdeye_headers(chain),
    )


# ── DexScreener ───────────────────────────────────────────────────────────────

async def _dexscreener_socials(mint: str) -> dict:
    """Pull social links (twitter/telegram/website) + image from DexScreener."""
    raw = await _get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
    pairs = raw.get("pairs") or [] if isinstance(raw, dict) else []
    for p in pairs:
        info = (p or {}).get("info") or {}
        socials = info.get("socials") or []
        websites = info.get("websites") or []
        if socials or websites:
            return {
                "socials": {
                    (s.get("type") or s.get("platform") or "link"): s.get("url")
                    for s in socials if isinstance(s, dict) and s.get("url")
                },
                "websites": [w.get("url") for w in websites if isinstance(w, dict) and w.get("url")],
                "imageUrl": info.get("imageUrl"),
            }
    return {}


# ── Curated KOL / influencer wallet registry ─────────────────────────────────
# Loaded once from app/data/kol_wallets.json: {"<wallet>": "<handle/name>"}.
# Empty by default — populate it to enable exact KOL attribution. Birdeye's own
# "whale"/"bundler"/"smart" trader tags already provide smart-money signal
# without this list; the registry adds named-KOL matching on top.
_KOL_WALLETS: dict[str, str] | None = None


def _load_kol_wallets() -> dict[str, str]:
    global _KOL_WALLETS
    if _KOL_WALLETS is None:
        try:
            _p = os.path.join(os.path.dirname(__file__), "..", "data", "kol_wallets.json")
            with open(_p, "r", encoding="utf-8") as f:
                _KOL_WALLETS = {str(k): str(v) for k, v in (json.load(f) or {}).items()}
        except Exception:
            _KOL_WALLETS = {}
    return _KOL_WALLETS


def _unwrap(x: Any) -> Any:
    """Safely unwrap a {'data': ...} envelope; return None on error/exception."""
    if isinstance(x, Exception) or x is None:
        return None
    if isinstance(x, dict) and "data" in x:
        return x["data"]
    return x


def _num(v: Any, default: float = 0.0) -> float:
    """Best-effort float parse (shared across analysis aggregates)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _enrich_pumpfun_ath(resp: Any) -> Any:
    """Pre-compute ATH drawdown for pump.fun token info so the LLM never has to
    divide by hand (a frequent source of ~1000x % errors). market_cap and
    ath_market_cap are both in the same quote unit, so the ratio is unit-free.
    Adds pct_of_ath, drawdown_from_ath_pct, and ath_market_cap_usd in place."""
    try:
        d = resp.get("data") if isinstance(resp, dict) and isinstance(resp.get("data"), dict) else resp
        if not isinstance(d, dict):
            return resp
        mc = _num(d.get("market_cap") or d.get("market_cap_quote"))
        ath = _num(d.get("ath_market_cap"))
        usd = _num(d.get("usd_market_cap"))
        if mc > 0 and ath > 0:
            pct = mc / ath * 100.0
            d["pct_of_ath"] = round(pct, 4 if pct < 1 else 2)
            d["drawdown_from_ath_pct"] = round(100.0 - pct, 2)
            if usd > 0:
                d["ath_market_cap_usd"] = round(ath / mc * usd, 2)
    except Exception:
        pass
    return resp


async def _dev_launch_history(creator: str) -> dict:
    """How many tokens this creator has launched — serial-launcher / rug signal.
    Uses Helius getAssetsByCreator (real on-chain data, no paid provider)."""
    if not creator or not HELIUS_KEY:
        return {}
    try:
        res = await _post(HELIUS_RPC, {
            "jsonrpc": "2.0", "id": 1, "method": "getAssetsByCreator",
            "params": {"creatorAddress": creator, "onlyVerified": False, "page": 1, "limit": 100},
        })
        d = (res or {}).get("result", {}) if isinstance(res, dict) else {}
        items = d.get("items") or []
        samples = []
        for it in items[:8]:
            md = ((it or {}).get("content") or {}).get("metadata") or {}
            samples.append(md.get("symbol") or md.get("name") or (it.get("id", "")[:8]))
        return {"tokens_created": int(d.get("total") or len(items)), "samples": samples}
    except Exception:
        return {}


async def _wallet_pnl_score(wallet: str) -> dict:
    """DIY smart-money score for a wallet from Birdeye PnL summary (win rate +
    realized profit). This is how we approximate GMGN 'smart money' with our
    own keys — no Nansen/GMGN needed."""
    try:
        r = await birdeye_wallet_pnl(wallet, duration="7d")
        s = (_unwrap(r) or {}).get("summary") or {}
        wr = (s.get("counts") or {}).get("win_rate")
        realized = (s.get("pnl") or {}).get("realized_profit_usd")
        return {
            "wallet": wallet,
            "win_rate_pct": round(float(wr) * 100, 1) if wr is not None else None,
            "realized_pnl_usd": round(float(realized), 0) if realized is not None else None,
            "unique_tokens": int((s.get("unique_tokens") or 0)),
        }
    except Exception:
        return {"wallet": wallet}


# ── Single-source query-tool registry ────────────────────────────────────────
# The 4-place rule (dispatch + QueryType enum + tool_selector tags + prompt doc)
# is the #1 source of "tool silently doesn't work" bugs. The @query_tool
# decorator collapses THREE of those four into one declaration at the function:
# params (for _DISPATCH), tags (for tool_selector), and — validated at import —
# the QueryType enum. Only the prose prompt doc stays separate (it can't be
# generated). New query tools SHOULD use this decorator instead of hand-editing
# _DISPATCH + tool_selector. `validate_tool_registry()` (in tool_selector) fails
# loudly at import if any dispatch tool is missing its enum value or tags.
_QUERY_TOOL_REGISTRY: dict[str, tuple] = {}  # name -> (fn, required, optional, tags)


def query_tool(required: list[str] | None = None,
               optional: list[str] | None = None,
               tags: set[str] | None = None):
    """Register a query_onchain tool in one place: params + tags live here, next
    to the implementation. Derives the _DISPATCH entry and the tool_selector tag
    set; the QueryType enum membership is cross-checked at import."""
    def _deco(fn):
        _QUERY_TOOL_REGISTRY[fn.__name__] = (
            fn, list(required or []), list(optional or []), frozenset(tags or set()),
        )
        return fn
    return _deco


@query_tool(required=["address"], optional=["chain"], tags={"analysis"})
async def token_deep_analysis(address: str, chain: str = "solana") -> dict:
    """GMGN-style deep token analysis. Fans out concurrently to overview,
    security, holder concentration, behavioural holder labels
    (sniper/bundler/insider/dev), top traders (whale/smart tags), KOL registry
    and socials — then returns a COMPACT, pre-computed summary with risk flags.
    Present this instead of a plain price/mcap readout when the user asks for a
    detailed / deep / GMGN-style analysis, or 'is there smart money / snipers /
    bundlers / KOLs'."""
    _labels = ["sniper", "bundler", "insider", "dev"]
    _res = await asyncio.gather(
        birdeye_token_overview(address, chain),
        birdeye_token_security(address, chain),
        birdeye_holder_distribution(address, mode="top", top_n=10, chain=chain),
        birdeye_token_top_traders(address),
        *[birdeye_holder_positions(address, labels=lb, chain=chain) for lb in _labels],
        _dexscreener_socials(address),
        birdeye_holder_profile(address, chain=chain),
        return_exceptions=True,
    )
    ov = _unwrap(_res[0]) or {}
    sec = _unwrap(_res[1]) or {}
    dist = _unwrap(_res[2]) or {}
    traders_d = _unwrap(_res[3]) or {}
    pos_by_label = {lb: (_unwrap(_res[4 + i]) or []) for i, lb in enumerate(_labels)}
    socials = _res[8] if not isinstance(_res[8], Exception) else {}
    hp = _unwrap(_res[9]) or {}

    kol = _load_kol_wallets()

    def _f(v: Any, default: float = 0.0) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    # ── Overview ──
    overview = {
        "price_usd": _f(ov.get("price")),
        "market_cap": _f(ov.get("marketCap") or ov.get("mc") or ov.get("realMc")),
        "liquidity_usd": _f(ov.get("liquidity")),
        "volume_24h_usd": _f(ov.get("v24hUSD") or ov.get("v24h")),
        "price_change_24h_pct": _f(ov.get("priceChange24hPercent")),
        "holder_count": int(_f(ov.get("holder") or ov.get("holders"))),
        "unique_wallets_24h": int(_f(ov.get("uniqueWallet24h"))),
        "trades_24h": int(_f(ov.get("trade24h"))),
    }

    # ── Momentum + trade pressure (all from overview — no extra API calls) ──
    momentum = {
        "price_change_1h_pct": round(_f(ov.get("priceChange1hPercent")), 2),
        "price_change_6h_pct": round(_f(ov.get("priceChange4hPercent") or ov.get("priceChange8hPercent")), 2),
        "price_change_24h_pct": round(_f(ov.get("priceChange24hPercent")), 2),
        "unique_wallets_24h_change_pct": round(_f(ov.get("uniqueWallet24hChangePercent")), 2),
        "trades_24h_change_pct": round(_f(ov.get("trade24hChangePercent")), 2),
    }
    _vbuy = _f(ov.get("vBuy24hUSD"))
    _vsell = _f(ov.get("vSell24hUSD"))
    trade_pressure = {
        "buy_volume_24h_usd": round(_vbuy, 2),
        "sell_volume_24h_usd": round(_vsell, 2),
        "buy_sell_vol_ratio": round(_vbuy / _vsell, 2) if _vsell > 0 else None,
        "buy_count_24h": int(_f(ov.get("buy24h"))),
        "sell_count_24h": int(_f(ov.get("sell24h"))),
    }
    # Age from holder-profile creation_time (falls back to overview if absent).
    _created = _f((hp.get("token") or {}).get("creation_time")) if isinstance(hp, dict) else 0.0
    _age_days = round((time.time() - _created) / 86400, 1) if _created > 0 else None
    _mc = overview["market_cap"]
    liquidity_health = {
        "age_days": _age_days,
        "liquidity_to_mcap_pct": round(overview["liquidity_usd"] / _mc * 100, 2) if _mc > 0 else None,
        "volume_to_mcap_pct": round(overview["volume_24h_usd"] / _mc * 100, 2) if _mc > 0 else None,
    }

    # ── Security / rug signals ──
    def _authority_active(*keys: str) -> bool:
        for k in keys:
            v = sec.get(k)
            if isinstance(v, str) and v and v.lower() not in ("null", "none", "11111111111111111111111111111111"):
                return True
        return False
    # NOTE: authority-active is judged ONLY by the authority pubkey field.
    # `mintTx`/`creationTx` are the mint/creation transaction SIGNATURES — always
    # present for any token — so including them here falsely reported "mint
    # authority active" on every pump.fun token (whose mintAuthority is null /
    # renounced). Check the authority field alone.
    security = {
        "creator": sec.get("creatorAddress"),
        "mint_authority_active": _authority_active("mintAuthority"),
        "freeze_authority_active": _authority_active("freezeAuthority"),
        "is_mutable": bool(sec.get("mutableMetadata")) if sec.get("mutableMetadata") is not None else None,
        "is_token_2022": bool(sec.get("isToken2022")) if sec.get("isToken2022") is not None else None,
        "transfer_fee_active": bool(sec.get("transferFeeEnable")) if sec.get("transferFeeEnable") is not None else None,
        "non_transferable": bool(sec.get("nonTransferable")) if sec.get("nonTransferable") is not None else None,
        "top10_holder_pct": _f(sec.get("top10HolderPercent")) * (100 if _f(sec.get("top10HolderPercent")) <= 1 else 1),
        "creator_pct": _f(sec.get("creatorPercentage")) * (100 if _f(sec.get("creatorPercentage")) <= 1 else 1),
    }

    # ── Concentration (top 10 wallets) ──
    dist_summary = dist.get("summary") if isinstance(dist, dict) else None
    top10_pct = _f((dist_summary or {}).get("percent_of_supply")) if dist_summary else security["top10_holder_pct"]
    concentration = {
        "top10_pct": round(top10_pct, 2),
        "top10_wallet_count": int((dist_summary or {}).get("wallet_count") or 0),
    }

    # ── Behavioural holder labels ──
    holder_labels: dict[str, dict] = {}
    for lb, rows in pos_by_label.items():
        rows = rows if isinstance(rows, list) else []
        holder_labels[lb] = {
            "count": len(rows),
            "pct_of_supply": round(sum(_f(r.get("percent_of_supply")) for r in rows if isinstance(r, dict)), 2),
        }

    # ── Top traders + smart/whale/KOL attribution ──
    trader_items = traders_d.get("items") if isinstance(traders_d, dict) else (traders_d if isinstance(traders_d, list) else [])
    trader_items = trader_items or []
    smart_count = 0
    kol_hits: list[dict] = []
    top_traders_out: list[dict] = []
    for t in trader_items[:15]:
        if not isinstance(t, dict):
            continue
        owner = t.get("owner") or t.get("address") or ""
        tags = t.get("tags") or []
        if any(str(tg).lower() in ("whale", "smart_money", "smartmoney", "bundler") for tg in tags):
            smart_count += 1
        if owner in kol:
            kol_hits.append({"wallet": owner, "handle": kol[owner]})
        top_traders_out.append({
            "wallet": owner,
            "tags": tags,
            "volume_usd": _f(t.get("volume")),
            "trades": int(_f(t.get("trade"))),
        })

    # Also match KOL registry against the top-10 holder list.
    for h in (dist.get("holders") or []) if isinstance(dist, dict) else []:
        w = h.get("wallet") if isinstance(h, dict) else None
        if w and w in kol and not any(k["wallet"] == w for k in kol_hits):
            kol_hits.append({"wallet": w, "handle": kol[w]})

    # ── Phase 2: dev launch history + DIY PnL-scored smart money ──
    # These need creator + trader owners known from phase 1, so they run in a
    # second concurrent batch. Both use our own keys (Helius + Birdeye PnL).
    _owners = [t["wallet"] for t in top_traders_out[:6] if t.get("wallet")]
    _p2 = await asyncio.gather(
        _dev_launch_history(security.get("creator") or ""),
        bundle_ring_analysis(address, chain),
        *[_wallet_pnl_score(w) for w in _owners],
        return_exceptions=True,
    )
    dev_profile = _p2[0] if _p2 and not isinstance(_p2[0], Exception) else {}
    _bundle = _p2[1] if len(_p2) > 1 and isinstance(_p2[1], dict) else {}
    bundle = {
        "verdict": _bundle.get("verdict"),
        "ring_count": _bundle.get("ring_count"),
        "largest_ring_wallets": _bundle.get("largest_ring_wallets"),
        "ringed_supply_pct": _bundle.get("ringed_supply_pct"),
    } if _bundle else {}
    _scores = [s for s in _p2[2:] if isinstance(s, dict)]
    # DIY "smart money" = net-profitable wallets with a real win rate.
    smart_money_wallets = [
        s for s in _scores
        if (s.get("realized_pnl_usd") or 0) > 0 and (s.get("win_rate_pct") or 0) >= 40
    ]
    # Fold DIY-profitable wallets that are also in the KOL registry into kol_hits.
    for s in smart_money_wallets:
        w = s.get("wallet")
        if w in kol and not any(k["wallet"] == w for k in kol_hits):
            kol_hits.append({"wallet": w, "handle": kol[w]})

    # ── Risk flags (computed, deterministic) ──
    risk_flags = {
        "high_concentration": concentration["top10_pct"] >= 30,
        "mint_authority_active": security["mint_authority_active"],
        "freeze_authority_active": security["freeze_authority_active"],
        "heavy_sniper_presence": holder_labels.get("sniper", {}).get("pct_of_supply", 0) >= 5,
        "insider_presence": holder_labels.get("insider", {}).get("count", 0) > 0,
        "bundle_detected": holder_labels.get("bundler", {}).get("count", 0) > 0,
        "low_liquidity": overview["liquidity_usd"] < 10_000,
        "serial_launcher_dev": (dev_profile.get("tokens_created") or 0) >= 5,
        "coordinated_bundle": bundle.get("verdict") == "high_risk_bundle",
        "heavy_sell_pressure": (trade_pressure["buy_sell_vol_ratio"] is not None
                                and trade_pressure["buy_sell_vol_ratio"] < 0.7),
        "transfer_fee_active": security["transfer_fee_active"] is True,
        "non_transferable": security["non_transferable"] is True,
    }

    # ── Computed quality score (0-100, deterministic) + flag lists ──
    _penalties = {
        "high_concentration": 18, "mint_authority_active": 15, "freeze_authority_active": 15,
        "heavy_sniper_presence": 10, "insider_presence": 10, "bundle_detected": 8,
        "low_liquidity": 15, "serial_launcher_dev": 12, "coordinated_bundle": 15,
        "heavy_sell_pressure": 8, "transfer_fee_active": 12, "non_transferable": 25,
    }
    _score = 100
    red_flags = []
    for flag, pen in _penalties.items():
        if risk_flags.get(flag):
            _score -= pen
            red_flags.append(flag)
    green_flags = []
    if smart_money_wallets:
        _score += 5; green_flags.append("profitable_smart_money_holders")
    if kol_hits:
        _score += 5; green_flags.append("kol_holders_present")
    if not security["mint_authority_active"] and not security["freeze_authority_active"]:
        green_flags.append("authorities_renounced")
    if concentration["top10_pct"] and concentration["top10_pct"] < 20:
        green_flags.append("healthy_distribution")
    _score = max(0, min(100, _score))
    _rating = ("high_risk" if _score < 40 else "caution" if _score < 65 else "moderate" if _score < 82 else "solid")
    quality = {"score": _score, "rating": _rating, "red_flags": red_flags, "green_flags": green_flags}

    return {
        "mint": address,
        "quality": quality,
        "overview": overview,
        "momentum": momentum,
        "trade_pressure": trade_pressure,
        "liquidity_health": liquidity_health,
        "security": security,
        "dev_profile": dev_profile,
        "concentration": concentration,
        "holder_labels": holder_labels,
        "bundle": bundle,
        "smart_money_trader_count": smart_count,
        "smart_money_wallets": smart_money_wallets,
        "top_traders": top_traders_out[:8],
        "kol_holders": kol_hits,
        "kol_registry_size": len(kol),
        "socials": socials or {},
        "risk_flags": risk_flags,
        "_note": (
            "quality.score = deterministic 0-100 (penalties for risk_flags, bonus for "
            "smart-money/KOL/renounced authorities); momentum+trade_pressure+liquidity_health "
            "from Birdeye overview; bundle = same-block coordinated-ring check; "
            "smart_money_wallets = DIY PnL-scored; dev_profile = Helius launch history."
        ),
    }


# ── Bundle-ring / coordinated-wallet detection ────────────────────────────────

@query_tool(required=["token_address"], optional=["chain"], tags={"analysis"})
async def bundle_ring_analysis(token_address: str, chain: str = "solana") -> dict:
    """Detect coordinated wallet rings (bundles / insider clusters) for a token.
    Pulls the sniper/bundler/insider holder set, then uses each wallet's FIRST
    acquisition block for THIS token (Birdeye first-funded) to find wallets that
    bought in the SAME block — the on-chain signature of a coordinated bundle.
    Real GMGN-style bundle/rug intel from our own keys (Birdeye + Helius)."""
    labels = ["bundler", "sniper", "insider"]
    _res = await asyncio.gather(
        *[birdeye_holder_positions(token_address, labels=lb, chain=chain) for lb in labels],
        return_exceptions=True,
    )
    # Distinct suspect wallets → strongest supply % + union of labels.
    suspects: dict[str, dict] = {}
    for lb, r in zip(labels, _res):
        rows = _unwrap(r) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            w = row.get("wallet_address")
            if not w:
                continue
            pct = _num(row.get("percent_of_supply"))
            cur = suspects.get(w)
            if cur is None:
                suspects[w] = {"labels": {lb}, "pct_of_supply": pct}
            else:
                cur["labels"].add(lb)
                cur["pct_of_supply"] = max(cur["pct_of_supply"], pct)

    wallets = list(suspects.keys())
    if not wallets:
        return {
            "mint": token_address, "suspect_wallets": 0, "ring_count": 0,
            "rings": [], "verdict": "clean",
            "note": "No bundler/sniper/insider-labelled wallets found for this token.",
        }

    # First acquisition block for THIS token, per wallet (Birdeye first-funded).
    acquire: dict[str, int] = {}
    for i in range(0, len(wallets), 40):
        chunk = wallets[i:i + 40]
        try:
            ff = await birdeye_wallet_first_funded(",".join(chunk), token_address=token_address, chain=chain)
            data = (ff or {}).get("data") or {}
            for w, info in data.items():
                if isinstance(info, dict) and info.get("block_number"):
                    acquire[w] = int(info["block_number"])
        except Exception:
            continue

    # Cluster suspect wallets by identical acquisition block = coordinated buy.
    by_block: dict[int, list[str]] = {}
    for w, blk in acquire.items():
        by_block.setdefault(blk, []).append(w)

    rings = []
    for block, members in by_block.items():
        if len(members) >= 2:
            supply = round(sum(suspects[w]["pct_of_supply"] for w in members if w in suspects), 3)
            rings.append({
                "acquire_block": block,
                "wallet_count": len(members),
                "pct_of_supply": supply,
                "wallets": members[:10],
            })
    rings.sort(key=lambda x: (x["wallet_count"], x["pct_of_supply"]), reverse=True)

    ringed_wallets = sum(x["wallet_count"] for x in rings)
    ringed_supply = round(sum(x["pct_of_supply"] for x in rings), 3)
    largest = rings[0]["wallet_count"] if rings else 0

    if largest >= 5 or ringed_supply >= 10:
        verdict = "high_risk_bundle"
    elif rings:
        verdict = "coordinated_activity_detected"
    else:
        verdict = "no_same_block_clusters"

    return {
        "mint": token_address,
        "suspect_wallets": len(wallets),
        "wallets_with_acquire_block": len(acquire),
        "ring_count": len(rings),
        "largest_ring_wallets": largest,
        "ringed_wallets_total": ringed_wallets,
        "ringed_supply_pct": ringed_supply,
        "rings": rings[:8],
        "verdict": verdict,
        "note": (
            "rings = groups of sniper/bundler/insider wallets that FIRST acquired "
            "this token in the same block (coordinated bundle). ringed_supply_pct = "
            "combined supply held by those wallets. Source: Birdeye holder-positions "
            "+ first-funded (our own keys, no paid provider)."
        ),
    }


# ── KOL / smart-money discovery feed ──────────────────────────────────────────

_KOL_FEED_STABLES = {
    "So11111111111111111111111111111111111111112",   # wSOL
    "So11111111111111111111111111111111111111111",   # native SOL (Birdeye alias)
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",   # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",   # USDT
}
_KOL_FEED_CACHE: dict = {"ts": 0.0, "hours": None, "buys": None}
_KOL_FEED_TTL = 120  # seconds — scanning the whole registry is expensive


async def _kol_recent_buys(wallet: str, handle: str, since_ts: int, sem: "asyncio.Semaphore") -> list[dict]:
    """Recent BUY-side swaps for one KOL wallet (token received, non-stable)."""
    async with sem:
        try:
            txs = await helius_wallet_txs(wallet, limit=25)
        except Exception:
            return []
    out: list[dict] = []
    if not isinstance(txs, list):
        return out
    for t in txs:
        if not isinstance(t, dict) or t.get("type") != "SWAP":
            continue
        ts = int(t.get("timestamp") or 0)
        if ts < since_ts:
            continue
        for tt in (t.get("tokenTransfers") or []):
            if not isinstance(tt, dict):
                continue
            if tt.get("toUserAccount") == wallet and tt.get("mint") not in _KOL_FEED_STABLES:
                out.append({"mint": tt["mint"], "handle": handle, "ts": ts})
                break  # one buy per swap tx
    return out


@query_tool(optional=["hours", "limit", "chain"], tags={"analysis"})
async def kol_discovery_feed(hours: int = 24, limit: int = 15, chain: str = "solana") -> dict:
    """What the tracked KOL / smart-money wallets are BUYING right now. Scans the
    curated KOL registry's recent on-chain swaps (Helius) and ranks tokens by how
    many DISTINCT KOLs bought them in the last `hours`. This is the 'smart money is
    buying X' discovery feed — real-time, from our own keys. Use it for questions
    like 'what are KOLs / smart money buying', 'what's smart money accumulating'."""
    kol = _load_kol_wallets()
    if not kol:
        return {"tokens": [], "wallets_scanned": 0,
                "note": "KOL registry (app/data/kol_wallets.json) is empty — populate it to enable this feed."}

    hours = max(1, min(int(hours), 168))
    limit = max(1, min(int(limit), 30))
    since = int(time.time()) - hours * 3600

    c = _KOL_FEED_CACHE
    if c["buys"] is not None and c["hours"] == hours and (time.time() - c["ts"]) < _KOL_FEED_TTL:
        buys = c["buys"]
    else:
        sem = asyncio.Semaphore(10)
        results = await asyncio.gather(
            *[_kol_recent_buys(w, h, since, sem) for w, h in kol.items()],
            return_exceptions=True,
        )
        buys = [b for r in results if isinstance(r, list) for b in r]
        c.update({"ts": time.time(), "hours": hours, "buys": buys})

    agg: dict[str, dict] = {}
    for b in buys:
        row = agg.setdefault(b["mint"], {"mint": b["mint"], "kols": set(), "buys": 0, "latest_ts": 0})
        row["kols"].add(b["handle"])
        row["buys"] += 1
        row["latest_ts"] = max(row["latest_ts"], b["ts"])

    ranked = sorted(agg.values(), key=lambda x: (len(x["kols"]), x["latest_ts"]), reverse=True)[:limit]

    async def _meta(mint: str) -> tuple[str, dict]:
        try:
            d = _unwrap(await birdeye_token_overview(mint, chain)) or {}
            return mint, {
                "symbol": d.get("symbol"), "name": d.get("name"),
                "market_cap": d.get("marketCap") or d.get("mc") or d.get("realMc"),
                "liquidity_usd": d.get("liquidity"),
            }
        except Exception:
            return mint, {}

    metas = dict(await asyncio.gather(*[_meta(x["mint"]) for x in ranked])) if ranked else {}

    tokens = []
    for x in ranked:
        md = metas.get(x["mint"], {})
        tokens.append({
            "mint": x["mint"],
            "symbol": md.get("symbol"),
            "name": md.get("name"),
            "kol_buyers": len(x["kols"]),
            "kols": sorted(x["kols"])[:12],
            "total_buys": x["buys"],
            "market_cap": md.get("market_cap"),
            "liquidity_usd": md.get("liquidity_usd"),
        })

    return {
        "window_hours": hours,
        "wallets_scanned": len(kol),
        "distinct_tokens_bought": len(agg),
        "tokens": tokens,
        "note": (
            "Ranked by number of DISTINCT KOLs buying in the window. "
            "Source: Helius parsed swaps across the curated KOL registry (our keys). "
            "kol_buyers >= 2-3 on a fresh low-cap = strong smart-money signal."
        ),
    }


async def dex_token(mint: str) -> dict:
    """All DEX pairs for a token — price, 24h volume, liquidity, market cap.

    Pre-aggregates to keep the response useful after truncation:
      - Solana pairs only
      - Sorted by liquidity (descending) so big pools survive any cap
      - `summary_by_dex`: total liquidity / 24h volume / pair count per DEX
      - `pairs`: top 25 trimmed to essential fields only
    """
    raw = await _get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
    pairs = raw.get("pairs") or [] if isinstance(raw, dict) else []
    sol_pairs = [p for p in pairs if isinstance(p, dict) and p.get("chainId") == "solana"]

    def _liq(p: dict) -> float:
        liq = p.get("liquidity") or {}
        try:
            return float(liq.get("usd") or 0)
        except (TypeError, ValueError):
            return 0.0

    def _vol(p: dict) -> float:
        vol = p.get("volume") or {}
        try:
            return float(vol.get("h24") or 0)
        except (TypeError, ValueError):
            return 0.0

    sol_pairs.sort(key=_liq, reverse=True)

    summary: dict[str, dict] = {}
    for p in sol_pairs:
        dex = (p.get("dexId") or "unknown").lower()
        s = summary.setdefault(dex, {"pairCount": 0, "totalLiquidityUsd": 0.0, "totalVolume24hUsd": 0.0})
        s["pairCount"] += 1
        s["totalLiquidityUsd"] += _liq(p)
        s["totalVolume24hUsd"] += _vol(p)

    summary_by_dex = [
        {"dex": dex, **{k: round(v, 2) if isinstance(v, float) else v for k, v in stats.items()}}
        for dex, stats in sorted(summary.items(), key=lambda kv: -kv[1]["totalLiquidityUsd"])
    ]

    trimmed_pairs = [
        {
            "dex": p.get("dexId"),
            "pairAddress": p.get("pairAddress"),
            "baseSymbol": (p.get("baseToken") or {}).get("symbol"),
            "quoteSymbol": (p.get("quoteToken") or {}).get("symbol"),
            "priceUsd": p.get("priceUsd"),
            "liquidityUsd": round(_liq(p), 2),
            "volume24hUsd": round(_vol(p), 2),
            "priceChange24h": (p.get("priceChange") or {}).get("h24"),
        }
        for p in sol_pairs[:25]
    ]

    return {
        "totalPairs": len(sol_pairs),
        "totalLiquidityUsd": round(sum(_liq(p) for p in sol_pairs), 2),
        "totalVolume24hUsd": round(sum(_vol(p) for p in sol_pairs), 2),
        "summaryByDex": summary_by_dex,
        "topPairs": trimmed_pairs,
    }


async def dex_search(query: str) -> dict:
    """Search DEX pairs by token name, symbol, or address."""
    data = await _get("https://api.dexscreener.com/latest/dex/search", params={"q": query})
    if isinstance(data, dict) and "pairs" in data:
        solana = [p for p in (data["pairs"] or []) if p.get("chainId") == "solana"]
        return {**data, "pairs": solana}
    return data


async def dex_trending() -> list:
    """Trending/boosted tokens on Solana DEXes."""
    return await _get("https://api.dexscreener.com/token-boosts/latest/v1")


async def dex_latest_pairs() -> list:
    """Newest created trading pairs on Solana DEXes."""
    return await _get("https://api.dexscreener.com/token-profiles/latest/v1")


# ── Helius ────────────────────────────────────────────────────────────────────

async def helius_token_holders(mint: str) -> list:
    """Top 20 token holders via Helius RPC."""
    result = await _post(HELIUS_RPC, {
        "jsonrpc": "2.0", "id": 1,
        "method": "getTokenLargestAccounts",
        "params": [mint],
    })
    return result.get("result", {}).get("value", result)


async def helius_token_supply(mint: str) -> dict:
    """Total supply and decimals for a token via Helius."""
    result = await _post(HELIUS_RPC, {
        "jsonrpc": "2.0", "id": 1,
        "method": "getTokenSupply",
        "params": [mint],
    })
    return result.get("result", {}).get("value", result)


async def helius_wallet_tokens(wallet: str) -> list:
    """All tokens held by a wallet — under both token programs.

    Same omission as the empty-account scan had: `getTokenAccountsByOwner`
    answers for exactly the program it is asked about, so querying only the
    legacy one hides every Token-2022 holding a wallet has.
    """
    out: list = []
    for program_id in _TOKEN_PROGRAMS:
        result = await _post(HELIUS_RPC, {
            "jsonrpc": "2.0", "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [wallet, {"programId": program_id}, {"encoding": "jsonParsed"}],
        })
        value = result.get("result", {}).get("value")
        if isinstance(value, list):
            out.extend(value)
    return out


async def helius_wallet_txs(wallet: str, limit: int = 20) -> dict | list:
    """Wallet transaction history — decoded type (SWAP, TRANSFER, STAKE), source protocol."""
    if not HELIUS_KEY:
        return {"error": "HELIUS_API_KEY not configured"}
    return await _get(
        f"{HELIUS_API}/addresses/{wallet}/transactions",
        params={"limit": min(limit, 50), "api-key": HELIUS_KEY},
    )


# ── Jupiter ───────────────────────────────────────────────────────────────────

_JUP_API_KEY = os.environ.get("JUPITER_API_KEY", "")

def _jup_headers() -> dict:
    return {"x-api-key": _JUP_API_KEY} if _JUP_API_KEY else {}


async def jup_price(mints: str) -> dict:
    """Real-time price for comma-separated mint addresses from Jupiter."""
    return await _get("https://api.jup.ag/price/v3", params={"ids": mints}, headers=_jup_headers())


async def jup_token_search(query: str) -> list:
    """Search tokens by name, symbol, or address via Jupiter."""
    return await _get("https://api.jup.ag/tokens/v2/search", params={"query": query}, headers=_jup_headers())


async def jup_trending(limit: int = 20) -> list:
    """Trending tokens on Jupiter by volume."""
    return await _get("https://api.jup.ag/tokens/v2/trending", params={"limit": limit}, headers=_jup_headers())


# ── Jupiter Portfolio API ─────────────────────────────────────────────────────
# Scope: Jupiter products ONLY (DCA, limit orders, perpetuals, lend,
# JUP / JupSOL stake, Jupiter LP). Cross-protocol coverage (Kamino, Meteora,
# etc.) lives in other tools. All endpoints require x-api-key; the lite host
# returns 404 on /portfolio/* paths.

async def jup_portfolio_positions(wallet: str, platforms: str = "") -> dict:
    """Wallet positions across Jupiter products (DCA / limit / perp / lend /
    stake / LP). Optional comma-separated `platforms` filter."""
    params: dict = {}
    if platforms:
        params["platforms"] = platforms
    return await _get(
        f"https://api.jup.ag/portfolio/v1/positions/{wallet}",
        params=params,
        headers=_jup_headers(),
    )


async def jup_staked_jup(wallet: str) -> dict:
    """JUP staking state: total staked + pending unstakes."""
    return await _get(
        f"https://api.jup.ag/portfolio/v1/staked-jup/{wallet}",
        headers=_jup_headers(),
    )


async def jup_portfolio_platforms() -> list:
    """Full catalog of platforms the Portfolio API knows about (id, name,
    logo, deprecation flag). Useful when the LLM needs to filter or label."""
    return await _get(
        "https://api.jup.ag/portfolio/v1/platforms",
        headers=_jup_headers(),
    )


# ── Protocol Guidance (claim / vote — no TX, just info) ──────────────────────

_CLAIM_GUIDANCE: dict[str, str] = {
    "marinade": (
        "Marinade rewards auto-compound into mSOL — your balance grows automatically. "
        "To exit, use marinade_unstake (instant) or marinade_delayed_unstake (3-4 days, no fee)."
    ),
    "jito": (
        "Jito MEV rewards are distributed automatically to jitoSOL holders. "
        "Your jitoSOL balance increases over time — no manual claim needed."
    ),
    "jupiter": (
        "JUP airdrop and staking rewards can be claimed at https://jup.ag/vote. "
        "Make sure your wallet is connected there."
    ),
    "kamino": (
        "Kamino lending yields auto-compound. KMNO staking rewards accrue in-protocol — "
        "use kamino_unstake to exit your position and collect."
    ),
    "orca": (
        "To collect Orca liquidity position fees and rewards, use orca_collect_fees "
        "or orca_collect_rewards with your position address."
    ),
    "meteora": (
        "To collect Meteora position fees, use meteora_claim_fees with your position address. "
        "For farm rewards, use meteora_claim_rewards."
    ),
    "marginfi": (
        "MarginFi emissions are claimed via marginfi_claim_emissions. "
        "Points (seasonal) are tracked off-chain — check app.marginfi.com."
    ),
}

_VOTE_PORTALS: dict[str, str] = {
    "jupiter": "https://vote.jup.ag",
    "jup": "https://vote.jup.ag",
    "marinade": "https://marinade.finance/governance",
    "mnde": "https://marinade.finance/governance",
    "kamino": "https://app.kamino.finance/governance",
    "kmno": "https://app.kamino.finance/governance",
    "orca": "https://governance.orca.so",
    "marginfi": "https://app.marginfi.com/governance",
    "mrfn": "https://app.marginfi.com/governance",
    "jito": "https://gov.jito.network",
}


async def claim_guidance(protocol: str, type: str = "rewards") -> dict:
    """Return protocol-specific claim instructions (no TX)."""
    protocol = protocol.lower().strip()
    guidance = _CLAIM_GUIDANCE.get(
        protocol,
        f"To claim {type} from {protocol}: use the {protocol} app directly, "
        f"or specify a protocol-specific action (e.g. orca_collect_fees, meteora_claim_fees).",
    )
    return {"protocol": protocol, "type": type, "guidance": guidance}


# The two token programs. Asking only the first one is how a wallet with 19
# closable accounts was told it had 5: Token-2022 mints hold their accounts
# under a different program, and `getTokenAccountsByOwner` returns exactly the
# program you ask about.
_TOKEN_PROGRAMS = (
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
)


async def scan_empty_accounts(wallet: str) -> dict:
    """Scan a wallet for zero-balance token accounts, under BOTH token programs.

    Each empty account can be closed via close_accounts to recover ~0.002 SOL
    in rent. Calls Solana RPC directly — no external API key needed.
    """
    accounts: list = []
    for program_id in _TOKEN_PROGRAMS:
        resp = await _post(SOLANA_RPC, {
            "jsonrpc": "2.0", "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [wallet, {"programId": program_id}, {"encoding": "jsonParsed"}],
        })
        accounts.extend(resp.get("result", {}).get("value", []))

    RENT_SOL = 0.00203928  # approximate rent-exemption for a single token account
    empty = []
    for acc in accounts:
        parsed = (
            acc.get("account", {})
               .get("data", {})
               .get("parsed", {})
               .get("info", {})
        )
        amount_str = parsed.get("tokenAmount", {}).get("amount", "1")
        try:
            amount = int(amount_str)
        except (ValueError, TypeError):
            amount = 1
        if amount == 0:
            empty.append({
                "mint": parsed.get("mint", ""),
                "tokenAccount": acc.get("pubkey", ""),
                "recoverableSol": RENT_SOL,
            })

    total = round(len(empty) * RENT_SOL, 6)
    return {
        "emptyAccounts": empty,
        "count": len(empty),
        "totalRecoverableSol": total,
        "note": (
            "Recover SOL by closing these accounts: "
            "execute_action('close_accounts', {'mints': ['MINT1', 'MINT2', ...]})"
        ),
    }


async def top_validators(limit: int = 20, sort_by: str = "stake") -> dict:
    """Solana validators to choose between, named and ranked.

    `sort_by` is one of "stake" (default), "apy" or "commission" — "the highest
    APY validator" is a different list from "the biggest validator", and the
    only way to answer either was to re-sort twenty anonymous rows by hand.

    Stakewiz is the source because it is the one that knows who a validator is:
    a name, a logo, measured uptime and a real APY. The Solana RPC knows none
    of that — `getVoteAccounts` returns identities and stake and nothing else —
    so it is the fallback, and it reports NO apy at all rather than the
    `7.0 * (1 - commission)` this function used to return. That number was a
    constant dressed as a yield: every zero-commission validator in the list
    came back as "~7.0% APY", which is why they all looked identical.
    """
    sort_by = (sort_by or "stake").strip().lower()
    rows: list[dict] = []

    try:
        data = await _get("https://api.stakewiz.com/validators")
        if isinstance(data, list):
            for v in data:
                if v.get("delinquent"):
                    continue
                raw_commission = v.get("commission")
                if raw_commission is None:
                    continue
                # Stakewiz quotes some validators in basis points (400, 10000).
                commission = float(raw_commission)
                if commission > 100:
                    commission /= 100
                commission = round(min(max(commission, 0), 100))
                if commission > _VALIDATOR_MAX_COMMISSION:
                    continue
                rows.append({
                    "voteAccount": v.get("vote_identity", ""),
                    "name": (v.get("name") or "").strip() or None,
                    "icon": v.get("image") or None,
                    "commission": commission,
                    "activatedStakeSol": round(float(v.get("activated_stake") or 0), 2),
                    # `total_apy` (staking + MEV) is the figure every validator
                    # dashboard headlines. We were reading `apy_estimate`, which
                    # excludes MEV — 5.24% where Helius and Stakewiz both show
                    # 5.47% for the same validator, so our list looked wrong
                    # next to theirs.
                    "apyEstimatePct": (
                        round(float(v["total_apy"]), 2) if v.get("total_apy") is not None
                        else round(float(v["apy_estimate"]), 2) if v.get("apy_estimate") is not None
                        else None
                    ),
                    "uptimePct": round(float(v["uptime"]), 2) if v.get("uptime") is not None else None,
                    "isJito": bool(v.get("is_jito")),
                })
    except Exception:
        rows = []

    if not rows:
        body = {"jsonrpc": "2.0", "id": 1, "method": "getVoteAccounts",
                "params": [{"commitment": "confirmed"}]}
        resp = await _post(SOLANA_RPC, body)
        for v in resp.get("result", {}).get("current", []):
            commission = v.get("commission", 100)
            if commission > _VALIDATOR_MAX_COMMISSION:
                continue
            rows.append({
                "voteAccount": v.get("votePubkey", ""),
                "name": None,
                "icon": None,
                "commission": commission,
                "activatedStakeSol": round(v.get("activatedStake", 0) / 1e9, 2),
                "apyEstimatePct": None,
                "uptimePct": None,
                "isJito": False,
            })

    if sort_by == "apy":
        # Unknown APY sorts last rather than first: an absent measurement is not
        # a good one. Stake breaks the ties, and the ties are the whole story
        # here — the top APY is shared by dozens of validators, so ordering on
        # APY alone surfaced 150k-SOL unknowns above Helius at the identical
        # rate. Same yield, so the tiebreak should be the one that differs.
        rows.sort(
            key=lambda r: (
                r["apyEstimatePct"] is not None,
                r["apyEstimatePct"] or 0,
                r["activatedStakeSol"],
            ),
            reverse=True,
        )
    elif sort_by == "commission":
        rows.sort(key=lambda r: (r["commission"], -r["activatedStakeSol"]))
    else:
        rows.sort(key=lambda r: r["activatedStakeSol"] * (1 - r["commission"] / 100), reverse=True)

    rows = rows[: int(limit)]
    return {
        "validators": rows,
        "count": len(rows),
        "sortedBy": sort_by,
    }


_STAKE_PROGRAM = "Stake11111111111111111111111111111111111111"
_STAKE_AUTH_OFFSET = 12  # staker pubkey starts at byte 12 in serialized stake account data
_EPOCH_MAX = "18446744073709551615"  # u64::MAX — means "not deactivating"


async def my_stake_accounts(wallet: str) -> dict:
    """Return all native stake accounts where the wallet is the staker authority.
    Calls Solana JSON-RPC getProgramAccounts with jsonParsed encoding.
    """
    body = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getProgramAccounts",
        "params": [
            _STAKE_PROGRAM,
            {
                "encoding": "jsonParsed",
                "filters": [
                    {"memcmp": {"offset": _STAKE_AUTH_OFFSET, "bytes": wallet}}
                ],
            },
        ],
    }
    resp = await _post(SOLANA_RPC, body)
    accounts = resp.get("result") or []

    stakes = []
    for acc in accounts:
        pubkey = acc.get("pubkey", "")
        lamports = acc.get("account", {}).get("lamports", 0)
        parsed = acc.get("account", {}).get("data", {}).get("parsed", {})
        state_type = parsed.get("type", "uninitialized")
        info = parsed.get("info", {})
        delegation = info.get("stake", {}).get("delegation", {})

        activation_epoch = delegation.get("activationEpoch", "")
        deactivation_epoch = delegation.get("deactivationEpoch", "")
        deactivating = bool(deactivation_epoch) and deactivation_epoch not in ("", _EPOCH_MAX)

        if state_type == "delegated":
            status = "deactivating" if deactivating else "active"
        elif state_type == "initialized":
            status = "inactive"
        else:
            status = state_type

        stakes.append({
            "stakeAccount": pubkey,
            "stakedSol": round(lamports / 1e9, 4),
            "status": status,
            "voteAccount": delegation.get("voter", ""),
            "activationEpoch": activation_epoch,
            "deactivationEpoch": deactivation_epoch if deactivating else None,
        })

    total = sum(s["stakedSol"] for s in stakes)
    return {
        "stakeAccounts": stakes,
        "count": len(stakes),
        "totalStakedSol": round(total, 4),
    }


async def vote_guidance(protocol: str, proposal: str = None, choice: str = None) -> dict:
    """Return governance voting portal URL and context (no TX)."""
    protocol = protocol.lower().strip()
    portal = _VOTE_PORTALS.get(protocol, "https://realms.today")
    return {
        "protocol": protocol,
        "proposal": proposal,
        "choice": choice,
        "portal_url": portal,
        "guidance": (
            f"Governance voting for {protocol} happens on-chain via {portal}. "
            f"Connect your wallet to the portal to cast your vote."
        ),
    }


async def _yield_comparison(token: str = "SOL", category: str = "") -> Any:
    """Fetch live APY comparison across staking/lending protocols."""
    from app.services.yield_aggregator import get_yield_comparison
    if not category:
        # Map common token requests to a category
        category = "liquid_staking" if token.upper() in ("SOL", "MSOL", "JITOSOL", "JUPSOL") else "lending"
    return await get_yield_comparison(category=category)


# ── Relay.link data queries ────────────────────────────────────────────────────

_RELAY_API = "https://api.relay.link"


async def relay_get_quote(
    originChainId: int = 900,
    destinationChainId: int = 1,
    originCurrency: str = "",
    destinationCurrency: str = "",
    amount: str = "",
    tradeType: str = "EXACT_INPUT",
    **kwargs: Any,
) -> Any:
    return await _get(f"{_RELAY_API}/quote", params={
        "originChainId": originChainId,
        "destinationChainId": destinationChainId,
        "originCurrency": originCurrency,
        "destinationCurrency": destinationCurrency,
        "amount": amount,
        "tradeType": tradeType,
        "user": kwargs.get("user", ""),
        **{k: v for k, v in kwargs.items() if k not in ("user",) and v is not None},
    })


async def relay_get_chains(includeChains: str = "", **_: Any) -> Any:
    params = {}
    if includeChains:
        params["includeChains"] = includeChains
    return await _get(f"{_RELAY_API}/chains", params=params or None)


async def relay_get_chains_liquidity(originChainId: int | None = None, **_: Any) -> Any:
    params = {}
    if originChainId is not None:
        params["originChainId"] = originChainId
    return await _get(f"{_RELAY_API}/chains/liquidity", params=params or None)


async def relay_get_currencies(chainId: int | None = None, **kwargs: Any) -> Any:
    params = {}
    if chainId is not None:
        params["chainId"] = chainId
    for k in ("term", "limit", "defaultList", "verified", "chainIds"):
        if k in kwargs and kwargs[k] is not None:
            params[k] = kwargs[k]
    return await _get(f"{_RELAY_API}/currencies", params=params or None)


async def relay_get_token_price(currency: str = "", chainId: int = 900, **_: Any) -> Any:
    return await _get(f"{_RELAY_API}/currencies/token-price", params={
        "currency": currency,
        "chainId": chainId,
    })


async def relay_get_requests(user: str = "", limit: int | None = None, **_: Any) -> Any:
    params: dict = {}
    if user:
        params["user"] = user
    if limit is not None:
        params["limit"] = limit
    return await _get(f"{_RELAY_API}/requests", params=params or None)


async def relay_intent_status(requestId: str = "", **_: Any) -> Any:
    return await _get(f"{_RELAY_API}/intents/status", params={"requestId": requestId})


async def relay_get_app_fee_balances(chainId: int | None = None, **_: Any) -> Any:
    params = {}
    if chainId is not None:
        params["chainId"] = chainId
    return await _get(f"{_RELAY_API}/app-fee-balances", params=params or None)


async def relay_get_swap_sources(chainId: int = 900, **_: Any) -> Any:
    return await _get(f"{_RELAY_API}/swap-sources", params={"chainId": chainId})


# ── Magic Eden data query wrappers ────────────────────────────────────────────
# These translate camelCase LLM params → snake_case magic_eden.py function args.

async def _me_wallet_offers_received(walletAddress: str, minPrice=None, maxPrice=None, offset=0, limit=100, sort="updatedAt", sortDirection="desc") -> Any:
    from app.clients.magic_eden import get_wallet_offers_received
    return await get_wallet_offers_received(walletAddress, min_price=minPrice, max_price=maxPrice, offset=int(offset), limit=int(limit), sort=sort, sort_direction=sortDirection)

async def _me_wallet_offers_made(walletAddress: str, minPrice=None, maxPrice=None, offset=0, limit=100, sort="bidAmount", sortDirection="desc") -> Any:
    from app.clients.magic_eden import get_wallet_offers_made
    return await get_wallet_offers_made(walletAddress, min_price=minPrice, max_price=maxPrice, offset=int(offset), limit=int(limit), sort=sort, sort_direction=sortDirection)

async def _me_wallet_activities(walletAddress: str, offset=0, limit=100) -> Any:
    from app.clients.magic_eden import get_wallet_activities
    return await get_wallet_activities(walletAddress, offset=int(offset), limit=int(limit))

async def _me_owner_activities(owner: str, createdAt=None) -> Any:
    from app.clients.magic_eden import get_owner_activities
    return await get_owner_activities(owner, created_at=createdAt)

async def _me_wallet(walletAddress: str) -> Any:
    from app.clients.magic_eden import get_wallet
    return await get_wallet(walletAddress)

async def _me_wallet_tokens(walletAddress: str, collectionSymbol=None, offset=0, limit=100, minPrice=None, maxPrice=None, listStatus=None, sort="updatedAt", sortDirection="desc") -> Any:
    from app.clients.magic_eden import get_wallet_tokens
    return await get_wallet_tokens(walletAddress, collection_symbol=collectionSymbol, offset=int(offset), limit=int(limit), min_price=minPrice, max_price=maxPrice, list_status=listStatus, sort=sort, sort_direction=sortDirection)

async def _me_token(tokenMint: str) -> Any:
    from app.clients.magic_eden import get_token
    return await get_token(tokenMint)

async def _me_token_listings(tokenMint: str, listingAggMode=False) -> Any:
    from app.clients.magic_eden import get_token_listings
    agg = str(listingAggMode).lower() in ("true", "1", "yes")
    return await get_token_listings(tokenMint, listing_agg_mode=agg)

async def _me_token_offers_received(tokenMint: str, minPrice=None, maxPrice=None, offset=0, limit=100, sort="updatedAt", sortDirection="desc") -> Any:
    from app.clients.magic_eden import get_token_offers_received
    return await get_token_offers_received(tokenMint, min_price=minPrice, max_price=maxPrice, offset=int(offset), limit=int(limit), sort=sort, sort_direction=sortDirection)

async def _me_token_activities(tokenMint: str, offset=0, limit=100) -> Any:
    from app.clients.magic_eden import get_token_activities
    return await get_token_activities(tokenMint, offset=int(offset), limit=int(limit))

# Magic Eden quotes prices in lamports on its stats endpoints and in SOL almost
# everywhere else. The model has no way to tell which it is looking at, and it
# guessed: a floor of 5_000_000 lamports was reported to the user as "5 SOL",
# a thousandfold overstatement of what the card beside it said. So the numbers
# reach the model already in SOL, under names that say so.
_ME_LAMPORT_FIELDS = (
    "floorPrice", "avgPrice24hr", "volumeAll", "volume24hr", "volume7d", "listedTotalValue",
)


def _me_prices_to_sol(payload: Any) -> Any:
    """Rename every lamport price to a `…Sol` field holding SOL.

    Applied to lists and nested dicts too: a collection list carries a floor
    per row, and one un-normalised row is enough for the model to quote a
    price a thousand times too high.
    """
    if isinstance(payload, list):
        return [_me_prices_to_sol(i) for i in payload]
    if not isinstance(payload, dict):
        return payload
    out = {}
    for key, value in payload.items():
        if key in _ME_LAMPORT_FIELDS and isinstance(value, (int, float)) and value >= 0:
            out[f"{key}Sol"] = round(value / 1e9, 9)
        elif isinstance(value, (dict, list)):
            out[key] = _me_prices_to_sol(value)
        else:
            out[key] = value
    return out


async def _me_collection_stats(symbol: str, timeWindow=None, listingAggMode=False) -> Any:
    from app.clients.magic_eden import get_collection_stats
    agg = str(listingAggMode).lower() in ("true", "1", "yes")
    return _me_prices_to_sol(await get_collection_stats(symbol, time_window=timeWindow, listing_agg_mode=agg))

async def _me_collection_activities(symbol: str, offset=0, limit=100) -> Any:
    from app.clients.magic_eden import get_collection_activities
    return await get_collection_activities(symbol, offset=int(offset), limit=int(limit))

async def _me_collections(offset=0, limit=200) -> Any:
    from app.clients.magic_eden import get_collections
    items, paging = await get_collections(offset=int(offset), limit=int(limit))
    return {"collections": [_me_prices_to_sol(i) for i in items], "paging": paging}

async def _me_collection_listings(symbol: str, offset=0, limit=20, minPrice=None, maxPrice=None, sort="listPrice", sortDirection="asc", listingAggMode=False) -> Any:
    from app.clients.magic_eden import get_collection_listings
    agg = str(listingAggMode).lower() in ("true", "1", "yes")
    return await get_collection_listings(symbol, offset=int(offset), limit=int(limit), min_price=minPrice, max_price=maxPrice, sort=sort, sort_direction=sortDirection, listing_agg_mode=agg)

async def _me_collections_batch_listings(symbols, offset=0, limit=20, minPrice=None, maxPrice=None, sort="listPrice", sortDirection="asc", listingAggMode=False) -> Any:
    from app.clients.magic_eden import get_collections_batch_listings
    import json as _json
    agg = str(listingAggMode).lower() in ("true", "1", "yes")
    if isinstance(symbols, list):
        syms = symbols
    else:
        s = str(symbols)
        try:
            decoded = _json.loads(s)
            syms = decoded if isinstance(decoded, list) else [x.strip() for x in s.split(",")]
        except Exception:
            syms = [x.strip() for x in s.split(",")]
    syms = [x for x in syms if x]
    return await get_collections_batch_listings(syms, offset=int(offset), limit=int(limit), min_price=minPrice, max_price=maxPrice, sort=sort, sort_direction=sortDirection, listing_agg_mode=agg)

async def _me_collection_holder_stats(symbol: str) -> Any:
    from app.clients.magic_eden import get_collection_holder_stats
    return await get_collection_holder_stats(symbol)

async def _me_collection_leaderboard(symbol: str, limit=100) -> Any:
    from app.clients.magic_eden import get_collection_leaderboard
    return await get_collection_leaderboard(symbol, limit=int(limit))

async def _me_collection_attributes(collectionSymbol: str) -> Any:
    from app.clients.magic_eden import get_collection_attributes
    return await get_collection_attributes(collectionSymbol)

async def _me_mmm_pools(collectionSymbol=None, owner=None, offset=0, limit=100, field=None, direction=None) -> Any:
    from app.clients.magic_eden import get_mmm_pools
    return await get_mmm_pools(collection_symbol=collectionSymbol, owner=owner, offset=int(offset), limit=int(limit), field=field, direction=direction)

async def _me_mmm_token_pools(mintAddress: str, limit=1) -> Any:
    from app.clients.magic_eden import get_mmm_token_pools
    return await get_mmm_token_pools(mintAddress, limit=int(limit))

# ── SNS (Bonfida Name Service) data queries ──────────────────────────────────

_SNS_PROXY = "https://sns-sdk-proxy.bonfida.workers.dev"


async def _sns_get(path: str) -> Any:
    return await _get(f"{_SNS_PROXY}/{path}")


async def sns_resolve(domain: str) -> dict:
    """Resolve a `.sol` name to its owner, through our own Solana service.

    This used to call Bonfida's public proxy, which now answers Cloudflare
    error 1042 on every path — so `toly.sol`, registered for years, came back
    "not registered". A confident wrong answer, not an outage: the chat told
    the user their recipient did not exist.

    The Rust service derives the registry PDA and reads the owner off the
    chain, so there is no third party left to go down.
    """
    d = domain.lower().removesuffix(".sol")
    try:
        data = await _solana_action_data("sns_resolve", {"domain": d})
        owner = (data or {}).get("owner") if isinstance(data, dict) else None
        if isinstance(owner, str) and 32 <= len(owner.strip()) <= 44:
            return {"domain": f"{d}.sol", "owner": owner.strip(), "registered": True}
        # An explicit `registered: false` is the chain's answer, not a failure.
        if isinstance(data, dict) and data.get("registered") is False:
            return {"domain": f"{d}.sol", "owner": None, "registered": False,
                    "note": "Domain not found or not registered"}
    except Exception:
        # Distinguishing "not registered" from "could not ask" matters — the
        # caller prints one of them to the user.
        return {"domain": f"{d}.sol", "owner": None, "registered": None,
                "note": "Could not check this name right now."}
    return {"domain": f"{d}.sol", "owner": None, "registered": False,
            "note": "Domain not found or not registered"}

async def sns_reverse_lookup(pubkey: str) -> dict:
    try:
        name = await _sns_get(f"reverse-lookup/{pubkey}")
        return {"pubkey": pubkey, "domain": f"{name}.sol"}
    except Exception:
        return {"pubkey": pubkey, "domain": None, "note": "No primary domain found for this address"}


async def sns_check_available(domain: str) -> dict:
    d = domain.lower().removesuffix(".sol")
    try:
        await _sns_get(f"resolve/{d}")
        return {"domain": f"{d}.sol", "available": False, "note": "Domain is already registered"}
    except Exception:
        return {"domain": f"{d}.sol", "available": True, "note": "Domain is available for registration"}


async def sns_domains(owner: str) -> dict:
    try:
        domains = await _sns_get(f"domains/{owner}")
        return {"owner": owner, "domains": [f"{d}.sol" for d in domains], "count": len(domains)}
    except Exception:
        return {"owner": owner, "domains": [], "count": 0}


async def sns_primary_domain(owner: str) -> dict:
    try:
        name = await _sns_get(f"favorite-domain/{owner}")
        return {"owner": owner, "primaryDomain": f"{name}.sol" if name else None}
    except Exception:
        return {"owner": owner, "primaryDomain": None, "note": "No primary domain set"}


async def sns_record(domain: str, record: str = "IPFS") -> dict:
    d = domain.lower().removesuffix(".sol")
    try:
        value = await _sns_get(f"record/{d}/{record}")
        return {"domain": f"{d}.sol", "record": record, "value": value}
    except Exception:
        return {"domain": f"{d}.sol", "record": record, "value": None, "note": "Record not found"}


async def sns_domain_info(domain: str) -> dict:
    d = domain.lower().removesuffix(".sol")
    result: dict = {"domain": f"{d}.sol"}
    try:
        owner = await _sns_get(f"resolve/{d}")
        result["owner"] = owner
        result["registered"] = True
    except Exception:
        result["owner"] = None
        result["registered"] = False
    try:
        result["subdomains"] = await _sns_get(f"subdomains/{d}")
    except Exception:
        result["subdomains"] = []
    return result


async def sns_subdomains(domain: str) -> dict:
    d = domain.lower().removesuffix(".sol")
    try:
        subs = await _sns_get(f"subdomains/{d}")
        return {"domain": f"{d}.sol", "subdomains": subs, "count": len(subs)}
    except Exception:
        return {"domain": f"{d}.sol", "subdomains": [], "count": 0}


# ── Dispatcher ────────────────────────────────────────────────────────────────

# Maps action_type → (function, required_params, optional_params)
# Derived from the @query_tool registry — single source for these tools' params
# + tags. REGISTRY_TAGS is consumed by tool_selector; _REGISTRY_DISPATCH is
# spread into _DISPATCH below.
_REGISTRY_DISPATCH: dict[str, tuple] = {
    name: (fn, req, opt) for name, (fn, req, opt, _tags) in _QUERY_TOOL_REGISTRY.items()
}
REGISTRY_TAGS: dict[str, frozenset] = {
    name: tags for name, (_fn, _req, _opt, tags) in _QUERY_TOOL_REGISTRY.items()
}


_DISPATCH: dict[str, tuple] = {
    **_REGISTRY_DISPATCH,
    "birdeye_price":          (birdeye_price,          ["address"],     ["check_liquidity"]),
    "birdeye_multi_price":    (birdeye_multi_price,    ["list_address"], ["chain"]),
    "birdeye_token_overview": (birdeye_token_overview, ["address"],     ["chain"]),
    "birdeye_token_metadata": (birdeye_token_metadata, ["address"],     ["chain"]),
    "birdeye_token_security": (birdeye_token_security, ["address"],     ["chain"]),
    "birdeye_ohlcv":          (birdeye_ohlcv,          ["address"],     ["type", "time_from", "time_to", "chain"]),
    "birdeye_token_trending": (birdeye_token_trending, [],              ["interval", "limit", "chain"]),
    "birdeye_new_listings":   (birdeye_new_listings,   [],              ["limit", "chain"]),
    "birdeye_token_holders":  (birdeye_token_holders,  ["address"],     ["limit"]),
    "birdeye_wallet_portfolio":(birdeye_wallet_portfolio,["wallet"],    ["chain"]),
    "birdeye_wallet_pnl":     (birdeye_wallet_pnl,     ["wallet"],      ["duration", "chain"]),
    "birdeye_wallet_pnl_details": (birdeye_wallet_pnl_details, ["wallet"], ["duration", "sort_by", "sort_type", "limit", "chain"]),
    "birdeye_wallet_first_funded":     (birdeye_wallet_first_funded,     ["wallets"],       ["token_address", "chain"]),
    "birdeye_wallet_net_worth_history":(birdeye_wallet_net_worth_history,["wallet"],        ["count", "direction", "type", "chain"]),
    "birdeye_holder_distribution":     (birdeye_holder_distribution,     ["token_address"], ["mode", "top_n", "min_percent", "max_percent", "chain"]),
    "birdeye_holder_positions":        (birdeye_holder_positions,        ["token_address"], ["labels", "chain"]),
    "birdeye_holder_profile":          (birdeye_holder_profile,          ["token_address"], ["interval", "chain"]),
    "birdeye_token_trade_data":        (birdeye_token_trade_data,        ["address"],       ["chain"]),
    "birdeye_smart_money":    (birdeye_smart_money,    [],              ["interval", "limit"]),
    "birdeye_token_top_traders":(birdeye_token_top_traders,["address"], ["time_frame", "limit"]),
    # token_deep_analysis / bundle_ring_analysis / kol_discovery_feed are
    # declared via @query_tool and merged in below (see _REGISTRY_DISPATCH).
    "birdeye_search":         (birdeye_search,         ["keyword"],     ["limit", "chain"]),
    "birdeye_price_history":  (birdeye_price_history,  ["address"],     ["type", "chain"]),
    "dex_token":              (dex_token,              ["mint"],        []),
    "dex_search":             (dex_search,             ["query"],       []),
    "dex_trending":           (dex_trending,           [],             []),
    "dex_latest_pairs":       (dex_latest_pairs,       [],             []),
    "helius_token_holders":   (helius_token_holders,   ["mint"],        []),
    "helius_token_supply":    (helius_token_supply,    ["mint"],        []),
    "helius_wallet_tokens":   (helius_wallet_tokens,   ["wallet"],      []),
    "helius_wallet_txs":      (helius_wallet_txs,      ["wallet"],      ["limit"]),
    "jup_price":              (jup_price,              ["mints"],       []),
    "jup_token_search":       (jup_token_search,       ["query"],       []),
    "jup_trending":           (jup_trending,           [],              ["limit"]),
    "jup_portfolio_positions":(jup_portfolio_positions,["wallet"],      ["platforms"]),
    "jup_staked_jup":         (jup_staked_jup,         ["wallet"],      []),
    "jup_portfolio_platforms":(jup_portfolio_platforms,[],              []),
    # Robust fallback chains — wrap multiple providers so a single outage
    # doesn't return empty/stale data. Defined in clients/multi_source.py.
    **__import__("app.clients.multi_source", fromlist=["DISPATCH_ENTRIES"]).DISPATCH_ENTRIES,
    # Wallet utility (wallet injected by message.py, not from LLM params)
    "scan_empty_accounts":    (scan_empty_accounts,    ["wallet"],      []),
    "my_stake_accounts":      (my_stake_accounts,      ["wallet"],      []),
    # Protocol guidance
    "claim":                  (claim_guidance,         ["protocol"],    ["type"]),
    "vote":                   (vote_guidance,          ["protocol"],    ["proposal", "choice"]),
    # Validator discovery
    "top_validators":         (top_validators,         [],              ["limit", "sortBy", "sort_by"]),
    # Yield / APY comparison (liquid staking + lending)
    "yield":                  (_yield_comparison,      [],              ["token", "category"]),
    # Relay.link cross-chain data queries
    "relay_get_quote":        (relay_get_quote,        [],              ["originChainId", "destinationChainId", "originCurrency", "destinationCurrency", "amount", "tradeType", "user"]),
    "relay_get_chains":       (relay_get_chains,       [],              ["includeChains"]),
    "relay_get_chains_liquidity": (relay_get_chains_liquidity, [],     ["originChainId"]),
    "relay_get_currencies":   (relay_get_currencies,   [],              ["chainId", "term", "limit", "defaultList", "verified", "chainIds"]),
    "relay_get_token_price":  (relay_get_token_price,  ["currency"],    ["chainId"]),
    "relay_get_requests":     (relay_get_requests,     [],              ["user", "limit"]),
    "relay_intent_status":    (relay_intent_status,    ["requestId"],   []),
    "relay_get_app_fee_balances": (relay_get_app_fee_balances, [],     ["chainId"]),
    "relay_get_swap_sources": (relay_get_swap_sources, [],              ["chainId"]),
    # Magic Eden — read-only NFT data queries (fetched inline, result interpreted by LLM)
    "me_wallet_offers_received":   (_me_wallet_offers_received,   ["walletAddress"], ["minPrice", "maxPrice", "offset", "limit", "sort", "sortDirection"]),
    "me_wallet_offers_made":       (_me_wallet_offers_made,       ["walletAddress"], ["minPrice", "maxPrice", "offset", "limit", "sort", "sortDirection"]),
    "me_wallet_activities":        (_me_wallet_activities,        ["walletAddress"], ["offset", "limit"]),
    "me_owner_activities":         (_me_owner_activities,         ["owner"],         ["createdAt"]),
    "me_wallet":                   (_me_wallet,                   ["walletAddress"], []),
    "me_wallet_tokens":            (_me_wallet_tokens,            ["walletAddress"], ["collectionSymbol", "offset", "limit", "minPrice", "maxPrice", "listStatus", "sort", "sortDirection"]),
    "me_token":                    (_me_token,                    ["tokenMint"],     []),
    "me_token_listings":           (_me_token_listings,           ["tokenMint"],     ["listingAggMode"]),
    "me_token_offers_received":    (_me_token_offers_received,    ["tokenMint"],     ["minPrice", "maxPrice", "offset", "limit", "sort", "sortDirection"]),
    "me_token_activities":         (_me_token_activities,         ["tokenMint"],     ["offset", "limit"]),
    "me_collection_stats":         (_me_collection_stats,         ["symbol"],        ["timeWindow", "listingAggMode"]),
    "me_collection_activities":    (_me_collection_activities,    ["symbol"],        ["offset", "limit"]),
    "me_collections":              (_me_collections,              [],                ["offset", "limit"]),
    "me_collection_listings":      (_me_collection_listings,      ["symbol"],        ["offset", "limit", "minPrice", "maxPrice", "sort", "sortDirection", "listingAggMode"]),
    "me_collections_batch_listings":(_me_collections_batch_listings,["symbols"],     ["offset", "limit", "minPrice", "maxPrice", "sort", "sortDirection", "listingAggMode"]),
    "me_collection_leaderboard":   (_me_collection_leaderboard,   ["symbol"],        ["limit"]),
    "me_collection_attributes":    (_me_collection_attributes,    ["collectionSymbol"], []),
    "me_mmm_pools":                (_me_mmm_pools,                [],                ["collectionSymbol", "owner", "offset", "limit", "field", "direction"]),
    "me_mmm_token_pools":          (_me_mmm_token_pools,          ["mintAddress"],   ["limit"]),
    # SNS (Bonfida Name Service) — read-only domain data, result interpreted by LLM
    "sns_resolve":        (sns_resolve,        ["domain"],          []),
    "sns_reverse_lookup": (sns_reverse_lookup, ["pubkey"],          []),
    "sns_check_available":(sns_check_available,["domain"],          []),
    "sns_domains":        (sns_domains,        ["owner"],           []),
    "sns_primary_domain": (sns_primary_domain, ["owner"],           []),
    "sns_record":         (sns_record,         ["domain"],          ["record"]),
    "sns_domain_info":    (sns_domain_info,    ["domain"],          []),
    "sns_subdomains":     (sns_subdomains,     ["domain"],          []),
    "sns_list":           (sns_domains,        ["owner"],           []),  # alias for sns_domains
}

# Read-only "actions" exposed to the LLM as data queries (query_onchain).
# Each one routes through gateway /actions/build and returns the data payload
# the Rust solana-service places in BuildResponse.preview.params (transaction is
# always None for these). Centralising the list here keeps the taxonomy honest:
# anything that doesn't sign a tx belongs to query_onchain, not execute_action.
SOLANA_ACTION_DATA_TYPES: frozenset[str] = frozenset({
    # Raydium read-only
    "raydium_get_pools", "raydium_search_pools", "raydium_swap_quote",
    "raydium_get_pool_info", "raydium_get_user_positions", "raydium_get_clmm_positions",
    "raydium_get_token_info", "raydium_get_platform_stats", "raydium_get_clmm_configs",
    "raydium_get_pools_by_lp", "raydium_get_pools_v2", "raydium_get_pool_keys",
    "raydium_get_pool_liquidity_history", "raydium_get_pool_position_history",
    "raydium_get_token_list", "raydium_get_token_prices",
    "raydium_get_farm_info", "raydium_get_farm_by_lp", "raydium_get_farm_keys",
    "raydium_get_ido_keys", "raydium_get_main_version", "raydium_get_rpcs",
    "raydium_get_chain_time", "raydium_get_stake_pools", "raydium_get_migrate_lp",
    "raydium_get_auto_fee", "raydium_get_cpmm_configs",
    # Token safety: reads the mint account for the facts that decide whether
    # money can be taken, enriched with holder concentration and verification.
    "token_safety", "honeypot_check", "scam_check", "rug_check",
    # Magic Eden — routed through the gateway to the Rust service, which
    # resolves the auction house / token account / referral off the live
    # listing. chat-service has its own Magic Eden client for the reads the
    # LLM answers in prose; these are the ones that render as a card.
    "me_collections",
    "me_collection_listings", "me_collection_activities", "me_collection_stats",
    "me_collection_attributes", "me_collection_leaderboard",
    "me_collection_holder_stats", "me_collection_sales_history",
    "me_trending_collections",
    "me_token", "me_token_activities", "me_token_listings",
    "me_token_offers_received",
    # me_wallet is NOT here: /wallets/{w} answers with {walletAddress} and
    # nothing else — a card echoing back the address the user just typed.
    # chat-service still serves it for prose.
    "me_wallet_tokens", "me_wallet_activities",
    "me_owner_activities", "me_wallet_offers_made", "me_wallet_offers_received",
    "me_mmm_pools",
    # The older spellings of the same reads. They were reachable only as
    # ACTIONS, which is why the query card answered them with "use the action
    # card" — a dead end for something that only ever returned data.
    "me_collection_info", "me_nft_info", "me_wallet_nfts",
    "me_collection_activity", "me_listings", "me_offers", "me_collection_nfts",
    # Orca read-only
    "orca_get_pools", "orca_get_pool", "orca_search_pools", "orca_get_user_positions",
    "orca_get_pool_positions", "orca_search_tokens", "orca_get_token", "orca_get_tokens",
    "orca_get_protocol_stats", "orca_get_orca_token", "orca_get_circulating_supply",
    "orca_get_total_supply", "orca_get_locked_liquidity",
    # Meteora read-only
    "meteora_dlmm_get_pairs", "meteora_dlmm_get_pair", "meteora_dlmm_get_user_positions",
    "meteora_dlmm_get_active_bin", "meteora_dlmm_get_pool_group", "meteora_dlmm_get_pool_groups",
    "meteora_dlmm_get_pool_ohlcv", "meteora_dlmm_get_pool_volume_history",
    "meteora_dlmm_get_protocol_stats",
    "meteora_dammv2_get_pool", "meteora_dammv2_get_pool_group", "meteora_dammv2_get_pool_groups",
    "meteora_dammv2_get_pool_ohlcv", "meteora_dammv2_get_pool_volume_history",
    "meteora_dammv2_get_pools", "meteora_dammv2_get_protocol_metrics",
    "meteora_dammv2_get_user_positions",
    "meteora_dammv1_get_alpha_vault_configs", "meteora_dammv1_get_alpha_vaults",
    "meteora_dammv1_get_farms", "meteora_dammv1_get_fee_config", "meteora_dammv1_get_pool_configs",
    "meteora_dammv1_get_pools", "meteora_dammv1_get_pools_by_vault_lp",
    "meteora_dammv1_get_pools_metrics", "meteora_dammv1_search_pools",
    "meteora_s2e_filter_vaults", "meteora_s2e_get_all_vaults", "meteora_s2e_get_analytics",
    "meteora_s2e_get_vault",
    "meteora_vault_get_addresses", "meteora_vault_get_apy", "meteora_vault_get_apy_history",
    "meteora_vault_get_info", "meteora_vault_get_state", "meteora_vault_get_virtual_price",
    # Kamino read-only
    "kamino_loan_detail", "kamino_market_detail", "kamino_markets",
    "kamino_market_metrics_history", "kamino_market_reserve_history",
    "kamino_market_reserves", "kamino_market_reserves_account", "kamino_market_leverage_metrics",
    "kamino_obligation_interest_earned", "kamino_obligation_interest_paid",
    "kamino_obligation_metrics_history", "kamino_obligation_pnl",
    "kamino_obligation_transactions",
    "kamino_open_borrow_orders", "kamino_borrow_order_fills",
    "kamino_oracle_prices", "kamino_principal_token_yields",
    "kamino_private_credit_metrics", "kamino_private_credit_metrics_history",
    "kamino_reserve_borrow_apy_history", "kamino_reserve_borrow_apy_median",
    "kamino_rewards_history", "kamino_rewards_list",
    "kamino_season_rewards_user", "kamino_season_rewards_vesting_pool",
    "kamino_staking_yields", "kamino_staking_yields_mean", "kamino_staking_yields_median",
    "kamino_usd_benchmark_rates",
    "kamino_user_farm_transactions", "kamino_user_klend_transactions",
    "kamino_user_klend_transactions_all", "kamino_user_kvault_rewards",
    "kamino_user_metrics_history", "kamino_user_obligations", "kamino_user_rewards",
    "kamino_user_staking_boosts", "kamino_user_transactions",
    "kamino_user_vault_metrics_history", "kamino_user_vault_pnl",
    "kamino_user_vault_pnl_history", "kamino_user_vault_position",
    "kamino_user_vault_positions",
    "kamino_vault_allocation_history", "kamino_vault_detail",
    "kamino_vault_metrics", "kamino_vault_metrics_history",
    "kamino_vault_mint_image", "kamino_vault_mint_metadata",
    "kamino_vault_transactions", "kamino_vaults",
    "kamino_vaults_rewards", "kamino_vaults_summary",
    "kamino_liquidity_strategies",
    "kamino_multiply_markets",
    "kamino_yield_history",
    "kamino_airdrop_allocations", "kamino_airdrop_metrics",
    "kamino_farm_transactions",
    # MarginFi read-only
    "marginfi_account_info", "marginfi_bank_detail", "marginfi_banks",
    "marginfi_health", "marginfi_points", "marginfi_user_accounts",
    # Jupiter data — most live in ActionType (action_schemas.py); only the
    # three portfolio reads (positions / staked-jup / platforms) are wired
    # through _DISPATCH so the LLM can call them directly without going
    # through the action-build path.
    "jup_dca_orders", "jup_limit_orders", "jup_tokens_tag",
    "jup_tokens_recent", "jup_tokens_trending",
    "jup_lend_positions", "jup_lend_earnings",
    "jup_pending_invites", "jup_lend_markets", "jup_platforms",
    # Pump.fun read-only — global curve constants for analytical math
    "pumpfun_curve_global",
    # Pump.fun / PumpSwap read-only queries (token info, discovery feeds,
    # bonding-curve state, search, comments, user history, AMM pool info).
    # Routed through /actions/build like curve_global; Rust returns the data
    # in preview.params with tx=None. Kept in sync with the pump.fun QueryType
    # members in services/action_schemas.py.
    "pumpfun_token_info", "pumpfun_trending", "pumpfun_new",
    "pumpfun_graduating", "pumpfun_koth", "pumpfun_search",
    "pumpfun_comments", "pumpfun_user", "pumpfun_bonding_curve",
    "pumpswap_pool_info",
})


def _coerce_numeric_params(params: dict) -> dict:
    """Convert numeric-looking string values back to int/float.

    Why: the LLM tool-call validator (`_validate_query_onchain`) coerces every
    incoming param value to `str()` so the action-side path always receives
    string params (Rust action layer expects `"amount": "10"`). The read-only
    Rust query handlers (`MeteoraDlmmGetPairsParams.page: Option<u32>`,
    `…limit: Option<u32>`, `Kamino…HealthHistoryParams.startTs: Option<i64>`)
    are strongly typed and reject string-encoded numbers with a 400 error.

    We can't change the validator (action path needs strings) and we don't
    want a per-action allow-list of "numeric-typed fields" (144 handlers,
    constant churn). Instead, do a conservative coercion at the boundary:
    if a string value parses cleanly as int or float, forward the parsed
    number. Strings that aren't numeric (token symbols, addresses, labels,
    "self", "auto", "all") pass through unchanged.

    Edge cases handled:
      • Solana addresses (32-44 base58 chars): some START with digits
        (e.g. `11111111111111111111111111111111` system program). The
        length cap below (max 18 chars for coercion) keeps them as strings.
      • Booleans-as-strings ("true"/"false") become real booleans so flags
        like `enableExpress` work whether sent as "true" or true.
      • Float exponents ("1e9") and signed numbers ("-100") parse correctly.
      • Empty strings, "self", "auto", "all", token symbols, anything with
        a non-digit char passes through untouched.
    """
    # Real numeric query params are page numbers, limits, offsets,
    # timestamps (10-13 digits), block numbers, basis points. All fit
    # comfortably under 18 chars. Solana addresses are 32-44 chars, so a
    # length cap is a safe filter against accidental address coercion.
    _MAX_NUMERIC_LEN = 18

    out: dict = {}
    for k, v in params.items():
        if not isinstance(v, str):
            out[k] = v
            continue
        s = v.strip()
        if s in ("true", "false"):
            out[k] = (s == "true")
            continue
        if len(s) > _MAX_NUMERIC_LEN:
            out[k] = v
            continue
        # Cheap fast-path: must start with a digit or sign char.
        if not s or (s[0] not in "+-" and not s[0].isdigit()):
            out[k] = v
            continue
        try:
            if "." not in s and "e" not in s and "E" not in s:
                out[k] = int(s)
                continue
            out[k] = float(s)
        except ValueError:
            out[k] = v
    return out


async def _solana_action_data(action_type: str, params: dict, wallet: str | None = None) -> Any:
    """Generic dispatcher for read-only Solana actions exposed as data queries.

    POSTs to gateway /actions/build and unwraps the query result. Rust returns
    BuildResponse with two payload locations:
      1) `data` (top-level)        — preferred; used by all the read-only
         get_* handlers (Meteora pools, Orca pools, Kamino markets, …).
         `preview.params` for these is just `{}`.
      2) `preview.params` (legacy) — a few older handlers stuff their result
         into the preview blob.
    We try (1) first, then (2), then return the whole envelope as a last
    resort so misshapen handlers still surface SOMETHING the LLM can reason
    over.
    """
    # Rust BuildRequest expects `{"type": ..., "params": ...}` — the struct
    # renames `action_type` to `"type"` via serde. Sending `"action_type"`
    # raises a 400 "missing field `type`" parse error and silently breaks
    # every read-only Solana query (Meteora pools, Kamino markets, Orca pools,
    # Raydium pools, etc.). The frontend uses `"type"`; we must too.
    #
    # Rust struct fields are strongly typed (`page: Option<u32>`, etc.) but
    # the upstream LLM tool-call validator stringifies every param value
    # (defensive normalisation for the action-side path which expects
    # `"amount": "10"` strings). For the read-only Rust handlers this
    # produces 400 errors like "invalid type: string \"1\", expected u32".
    # Coerce numeric-looking strings back to numbers before forwarding.
    body = {"type": action_type, "params": _coerce_numeric_params(params or {})}
    headers: dict[str, str] = {}
    if INTERNAL_KEY:
        headers["x-internal-api-key"] = INTERNAL_KEY
    # Solana-service auth middleware requires `x-user-wallet`. Many read-only
    # queries don't actually scan a user wallet (DLMM pool list, Kamino
    # markets, Orca tokens, etc.) but the middleware is uniform — every
    # authenticated route demands the header. Pass through the caller's
    # connected wallet if provided, otherwise fall back to a placeholder
    # the middleware accepts for non-wallet-scoped reads.
    if wallet:
        headers["x-user-wallet"] = wallet
    elif "wallet" in body["params"] and isinstance(body["params"]["wallet"], str):
        headers["x-user-wallet"] = body["params"]["wallet"]
    else:
        headers["x-user-wallet"] = "11111111111111111111111111111111"  # System program — neutral filler
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(f"{SOLANA_SERVICE_URL}/actions/build", json=body, headers=headers)
        r.raise_for_status()
        resp = r.json()

    if isinstance(resp, dict):
        # 1) Preferred: top-level `data` carries the actual query result.
        if resp.get("data") is not None:
            return resp["data"]
        # 2) Legacy: a few older Rust handlers put data in preview.params.
        preview = resp.get("preview")
        if isinstance(preview, dict):
            params_blob = preview.get("params")
            # Treat empty `{}` as "not the result" — those handlers stuff the
            # real data in `data` and leave preview.params as a placeholder.
            if params_blob not in (None, {}, []):
                return params_blob
    return resp


# All action types this module handles (specific dispatchers + generic Rust passthrough)
MARKET_DATA_TYPES: frozenset[str] = frozenset(_DISPATCH.keys()) | SOLANA_ACTION_DATA_TYPES


async def call(action_type: str, params: dict, wallet: str | None = None) -> Any:
    """Dispatch action_type to the corresponding market data function.
    Returns raw data (dict/list). Raises KeyError if action_type unknown,
    ValueError if required param missing, httpx.HTTPError on API failure.

    *wallet* is the authenticated user wallet — forwarded as `x-user-wallet`
    on direct Solana-service calls (the Rust auth middleware requires it).
    """
    # Token-identifier aliasing: the LLM uses mint / address / token_address
    # interchangeably (esp. `mint` for pump.fun tokens). Fill any missing synonym
    # from a present one so a tool that wants `address` still fires when the model
    # passed `mint` — without this, token_deep_analysis silently ValueError'd and
    # the assistant fell back to a shallow readout.
    _ids = ("address", "token_address", "mint")
    _present = next((params[k] for k in _ids if params.get(k)), None)
    if _present is not None:
        params = dict(params)
        for _k in _ids:
            params.setdefault(_k, _present)

    # Direct Solana-service passthrough for read-only Rust handlers. Bypasses
    # the gateway (CSRF + browser-JWT layer is for browsers, not internal
    # services). Auth uses the shared `x-internal-api-key` instead.
    if action_type in SOLANA_ACTION_DATA_TYPES:
        raw = await _solana_action_data(action_type, params, wallet=wallet)
        if action_type == "pumpfun_token_info":
            raw = _enrich_pumpfun_ath(raw)
        # Magic Eden reads come back from the Rust service with prices in
        # lamports. The card converts them; the model was handed the bare
        # number and read 5_000_000 as "5 SOL". Normalise here, on the way to
        # the model only — the card fetches this payload separately and does
        # its own conversion, so touching the Rust side would double it.
        if action_type.startswith("me_"):
            raw = _me_prices_to_sol(raw)
        return _cap(raw)

    if action_type not in _DISPATCH:
        raise KeyError(f"Unknown market data action: {action_type}")

    fn, required, optional = _DISPATCH[action_type]
    kwargs: dict = {}

    for key in required:
        val = params.get(key)
        if val is None:
            raise ValueError(f"'{action_type}' requires param '{key}'")
        kwargs[key] = val

    for key in optional:
        val = params.get(key)
        if val is not None:
            kwargs[key] = val

    raw = await fn(**kwargs)
    return _cap(raw)
