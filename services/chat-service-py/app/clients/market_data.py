"""
Market data client — aggregates Birdeye, DexScreener, Helius, Jupiter data APIs.

These functions are called directly by chat-service-py when the LLM invokes
a data-query action type (birdeye_price, dex_token, helius_wallet_txs, etc.).
Results are formatted as text and streamed inline in chat — no action card.

API keys: BIRDEYE_API_KEY, HELIUS_API_KEY (from environment).
"""

import json
import logging
import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from prometheus_client import Counter, Histogram

_log = logging.getLogger(__name__)

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
SOLANA_SERVICE_URL = os.environ.get("SOLANA_SERVICE_URL", "http://localhost:3030")
INTERNAL_KEY  = os.environ.get("OPRAI_INTERNAL_API_KEY", "")
TIMEOUT       = 12.0
_MAX_CHARS    = 6_000
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
    """All SPL tokens held by a wallet via Helius."""
    result = await _post(HELIUS_RPC, {
        "jsonrpc": "2.0", "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [wallet, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"}, {"encoding": "jsonParsed"}],
    })
    return result.get("result", {}).get("value", result)


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


async def scan_empty_accounts(wallet: str) -> dict:
    """Scan a wallet for zero-balance SPL token accounts.
    Each empty account can be closed via close_accounts to recover ~0.002 SOL in rent.
    Calls Solana RPC directly — no external API key needed.
    """
    body = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            wallet,
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"},
        ],
    }
    resp = await _post(SOLANA_RPC, body)
    accounts = resp.get("result", {}).get("value", [])

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


async def top_validators(limit: int = 20) -> dict:
    """Return top Solana validators sorted by effective stake (stake × (1-commission/100)).
    Filtered to commission ≤ 10%. Calls Solana JSON-RPC directly — no API key needed.
    Designed for native_stake validator selection UI.
    """
    body = {"jsonrpc": "2.0", "id": 1, "method": "getVoteAccounts", "params": [{"commitment": "confirmed"}]}
    resp = await _post(SOLANA_RPC, body)
    current = resp.get("result", {}).get("current", [])

    rows = []
    for v in current:
        commission = v.get("commission", 100)
        if commission > _VALIDATOR_MAX_COMMISSION:
            continue
        stake_sol = v.get("activatedStake", 0) / 1e9
        apy = round((1 - commission / 100) * _VALIDATOR_BASE_APY, 2)
        epoch_credits = v.get("epochCredits", [])
        recent_credits = 0
        if epoch_credits:
            _, credits, prev = epoch_credits[-1]
            recent_credits = credits - prev
        rows.append({
            "voteAccount": v.get("votePubkey", ""),
            "commission": commission,
            "activatedStakeSol": round(stake_sol, 2),
            "apyEstimatePct": apy,
            "epochCreditsRecent": recent_credits,
        })

    rows.sort(key=lambda r: r["activatedStakeSol"] * (1 - r["commission"] / 100), reverse=True)
    rows = rows[: int(limit)]
    return {"validators": rows, "count": len(rows),
            "note": "APY is an estimate based on ~7% network inflation; actual rewards vary."}


_STAKE_PROGRAM = "Stake11111111111111111111111111111111111111111"
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

async def _me_wallet_escrow_balance(walletAddress: str) -> Any:
    from app.clients.magic_eden import get_wallet_escrow_balance
    return await get_wallet_escrow_balance(walletAddress)

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

async def _me_collection_stats(symbol: str, timeWindow=None, listingAggMode=False) -> Any:
    from app.clients.magic_eden import get_collection_stats
    agg = str(listingAggMode).lower() in ("true", "1", "yes")
    return await get_collection_stats(symbol, time_window=timeWindow, listing_agg_mode=agg)

async def _me_collection_activities(symbol: str, offset=0, limit=100) -> Any:
    from app.clients.magic_eden import get_collection_activities
    return await get_collection_activities(symbol, offset=int(offset), limit=int(limit))

async def _me_collections(offset=0, limit=200) -> Any:
    from app.clients.magic_eden import get_collections
    items, paging = await get_collections(offset=int(offset), limit=int(limit))
    return {"collections": items, "paging": paging}

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

async def _me_launchpad_collections(offset=0, limit=200) -> Any:
    from app.clients.magic_eden import get_launchpad_collections
    return await get_launchpad_collections(offset=int(offset), limit=int(limit))

async def _me_collection_attributes(collectionSymbol: str) -> Any:
    from app.clients.magic_eden import get_collection_attributes
    return await get_collection_attributes(collectionSymbol)

async def _me_mmm_pools(collectionSymbol=None, owner=None, offset=0, limit=100, field=None, direction=None) -> Any:
    from app.clients.magic_eden import get_mmm_pools
    return await get_mmm_pools(collection_symbol=collectionSymbol, owner=owner, offset=int(offset), limit=int(limit), field=field, direction=direction)

async def _me_mmm_token_pools(mintAddress: str, limit=1) -> Any:
    from app.clients.magic_eden import get_mmm_token_pools
    return await get_mmm_token_pools(mintAddress, limit=int(limit))

async def _me_marketplace_popular(timeRange=None) -> Any:
    from app.clients.magic_eden import get_marketplace_popular_collections
    return await get_marketplace_popular_collections(time_range=timeRange)


# ── SNS (Bonfida Name Service) data queries ──────────────────────────────────

_SNS_PROXY = "https://sns-sdk-proxy.bonfida.workers.dev"


async def _sns_get(path: str) -> Any:
    return await _get(f"{_SNS_PROXY}/{path}")


async def sns_resolve(domain: str) -> dict:
    d = domain.lower().removesuffix(".sol")
    try:
        owner = await _sns_get(f"resolve/{d}")
        return {"domain": f"{d}.sol", "owner": owner, "registered": True}
    except Exception:
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
_DISPATCH: dict[str, tuple] = {
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
    "birdeye_smart_money":    (birdeye_smart_money,    [],              ["interval", "limit"]),
    "birdeye_token_top_traders":(birdeye_token_top_traders,["address"], ["time_frame", "limit"]),
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
    "top_validators":         (top_validators,         [],              ["limit"]),
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
    "me_wallet_escrow_balance":    (_me_wallet_escrow_balance,    ["walletAddress"], []),
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
    "me_collection_holder_stats":  (_me_collection_holder_stats,  ["symbol"],        []),
    "me_collection_leaderboard":   (_me_collection_leaderboard,   ["symbol"],        ["limit"]),
    "me_launchpad_collections":    (_me_launchpad_collections,    [],                ["offset", "limit"]),
    "me_collection_attributes":    (_me_collection_attributes,    ["collectionSymbol"], []),
    "me_mmm_pools":                (_me_mmm_pools,                [],                ["collectionSymbol", "owner", "offset", "limit", "field", "direction"]),
    "me_mmm_token_pools":          (_me_mmm_token_pools,          ["mintAddress"],   ["limit"]),
    "me_marketplace_popular":      (_me_marketplace_popular,      [],                ["timeRange"]),
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
    "kamino_yield_history",
    "kamino_airdrop_allocations", "kamino_airdrop_metrics",
    "kamino_farm_transactions",
    # MarginFi read-only
    "marginfi_account_info", "marginfi_bank_detail", "marginfi_banks",
    "marginfi_health", "marginfi_points", "marginfi_user_accounts",
    # Jupiter data (currently in ActionType, never in QueryType/_DISPATCH)
    "jup_dca_orders", "jup_limit_orders", "jup_tokens_tag",
    "jup_tokens_recent", "jup_tokens_trending", "jup_portfolio_positions",
    "jup_staked_jup", "jup_lend_positions", "jup_lend_earnings",
    "jup_pending_invites", "jup_lend_markets", "jup_platforms",
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
    # Direct Solana-service passthrough for read-only Rust handlers. Bypasses
    # the gateway (CSRF + browser-JWT layer is for browsers, not internal
    # services). Auth uses the shared `x-internal-api-key` instead.
    if action_type in SOLANA_ACTION_DATA_TYPES:
        return _cap(await _solana_action_data(action_type, params, wallet=wallet))

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
