"""
DeFi tool implementations — direct protocol APIs, no API-key gatekeeping.

Sources:
  Jupiter         api.jup.ag/price/v3, api.jup.ag/tokens/v2/*
  DexScreener     api.dexscreener.com
  Raydium         api.raydium.io
  Orca            api.orca.so
  Kamino          api.kamino.finance
  Marinade        api.marinade.finance
  Solend          api.solend.fi
  Helius          mainnet.helius-rpc.com  (RPC — needs API key)
  Birdeye         public-api.birdeye.so  (needs BIRDEYE_API_KEY)
  Jito            via OPRAI gateway  (already working)
  Security        via OPRAI gateway  (rugcheck)
  Portfolio       via OPRAI gateway  (auth-required)
"""

import httpx
import json
import os
from datetime import date as _date
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"), override=False)
except ImportError:
    pass

GATEWAY       = os.environ.get("GATEWAY_URL", "http://localhost:3001")
HELIUS_RPC    = f"https://mainnet.helius-rpc.com/?api-key={os.environ.get('HELIUS_API_KEY', '')}"
HELIUS_API    = f"https://api.helius.xyz/v0"
HELIUS_KEY    = os.environ.get("HELIUS_API_KEY", "")
BIRDEYE_BASE  = "https://public-api.birdeye.so"
BIRDEYE_KEY   = os.environ.get("BIRDEYE_API_KEY", "")
TENSOR_BASE   = "https://api.mainnet.tensordev.io/api/v1"
TENSOR_KEY    = os.environ.get("TENSOR_API_KEY", "")
KOBE          = "https://kobe.mainnet.jito.network"
TIMEOUT       = 12.0

# Legacy alias kept for compatibility
HELIUS = HELIUS_RPC
_MAX_TOOL_CHARS = 8_000

# ─── Low-level helpers ────────────────────────────────────────────────────────

async def _get(url: str, params: dict = None, headers: dict = None) -> Any:
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(url, params=clean, headers=headers or {})
        r.raise_for_status()
        return r.json()

async def _post(url: str, body: dict, headers: dict = None, params: dict = None) -> Any:
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(url, json=body, headers=headers or {}, params=clean)
        r.raise_for_status()
        return r.json()

def _auth(jwt: str) -> dict:
    return {"Authorization": f"Bearer {jwt}"}

def _cap(data: Any, max_chars: int = _MAX_TOOL_CHARS) -> Any:
    """Truncate large tool responses so OpenAI context limits are never hit."""
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
        # First try truncating any list values inside the dict (e.g. {"results": [...huge...]})
        truncated = {}
        for k, v in data.items():
            truncated[k] = _cap(v, max_chars) if isinstance(v, (list, dict)) else v
        if len(json.dumps(truncated, default=str)) <= max_chars:
            return truncated
        # Fall back: binary-search over key-value pairs
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


# ─── Jupiter ──────────────────────────────────────────────────────────────────

_JUP_API_KEY = os.environ.get("JUPITER_API_KEY", "")


def _jup_headers() -> dict:
    if _JUP_API_KEY:
        return {"x-api-key": _JUP_API_KEY}
    return {}


async def jup_prices(mints: str) -> dict:
    """Real-time price for comma-separated mint addresses (max 50).
    Returns per-token: usdPrice, priceChange24h (24h %), liquidity (USD), decimals, blockId, createdAt.
    Tokens without reliable recent pricing are excluded from the response."""
    return await _get("https://api.jup.ag/price/v3", params={"ids": mints}, headers=_jup_headers())


async def jup_search(query: str) -> list:
    """Search tokens by name, symbol, or comma-separated mint addresses (max 100).
    Returns: id, name, symbol, usdPrice, mcap, fdv, holderCount, liquidity,
    stats5m/1h/6h/24h (priceChange, buyVolume, sellVolume, numBuys, numSells, numTraders),
    audit (mintAuthorityDisabled, freezeAuthorityDisabled, topHoldersPercentage),
    organicScore, organicScoreLabel, isVerified, tags, launchpad, twitter, website."""
    return await _get("https://api.jup.ag/tokens/v2/search", params={"query": query}, headers=_jup_headers())


async def jup_recent() -> list:
    """Most recently launched tokens (always returns 30) — tokens that recently had first pool created.
    Includes pump.fun, letsbonk.fun, Raydium LaunchLab with live price, volume, audit data."""
    return await _get("https://api.jup.ag/tokens/v2/recent", headers=_jup_headers())


async def jup_trending(category: str = "toptrending", interval: str = "1h", limit: int = 50) -> list:
    """Top tokens by category and time interval from Jupiter Tokens v2.
    category: toptrending | toptraded | toporganicscore
    interval: 5m | 1h | 6h | 24h
    limit: 1-100 (default 50). Filters out SOL/USDC generic tokens.
    """
    data = await _get(
        f"https://api.jup.ag/tokens/v2/{category}/{interval}",
        params={"limit": limit},
        headers=_jup_headers(),
    )
    if isinstance(data, list):
        return data
    return data


async def jup_tokens_tag(query: str, limit: int = 50) -> list:
    """List tokens by tag from Jupiter Tokens v2.
    query: lst (liquid staking tokens) | verified (audited projects)
    These are the only two supported tags per Jupiter API.
    Returns full token data: price, mcap, audit, organic score, stats.
    """
    data = await _get("https://api.jup.ag/tokens/v2/tag", params={"query": query}, headers=_jup_headers())
    if isinstance(data, list):
        return data[:limit]
    return data


async def jup_verify_eligibility(token_id: str) -> dict:
    """Check if a token is eligible for Jupiter verification.
    Returns: tokenExists, isVerified, canVerify, canMetadata, verificationError, metadataError.
    Requires JUPITER_API_KEY.
    """
    if not _JUP_API_KEY:
        return {"error": "JUPITER_API_KEY not configured — add it to .env"}
    return await _get(
        "https://api.jup.ag/tokens/v2/verify/express/check-eligibility",
        params={"tokenId": token_id},
        headers=_jup_headers(),
    )


async def jup_quote(
    input_mint: str,
    output_mint: str,
    amount: int,
    swap_mode: str = "ExactIn",
    slippage_bps: int = 50,
) -> dict:
    """Jupiter swap quote — best route across all Solana DEXes.
    Preferred for generic 'how much X for Y' swap questions.
    input_mint / output_mint: token mint addresses.
    amount: in base units (SOL ×1e9, USDC/USDT ×1e6, most SPL ×1e6).
    swap_mode: 'ExactIn' (default) or 'ExactOut'.
    slippage_bps: slippage tolerance in basis points (default 50 = 0.5%)."""
    params: dict = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "swapMode": swap_mode,
        "slippageBps": slippage_bps,
    }
    return await _get("https://api.jup.ag/swap/v1/quote", params=params, headers=_jup_headers())


# ─── DexScreener ──────────────────────────────────────────────────────────────

async def dex_trending() -> list:
    """Trending/boosted tokens on Solana DEXes from DexScreener (high momentum, paid boosts)."""
    return await _get("https://api.dexscreener.com/token-boosts/latest/v1")


async def dex_search(query: str) -> dict:
    """Search Solana DEX pairs by token name, symbol, or address."""
    data = await _get("https://api.dexscreener.com/latest/dex/search", params={"q": query})
    if isinstance(data, dict) and "pairs" in data:
        solana_pairs = [p for p in (data["pairs"] or []) if p.get("chainId") == "solana"]
        return {**data, "pairs": solana_pairs}
    return data


async def dex_token(mint: str) -> dict:
    """All DEX pairs for a token — price per pair, 24h volume, liquidity, tx counts, market cap."""
    return await _get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")


async def dex_latest_pairs() -> list:
    """Newest created trading pairs on Solana DEXes."""
    return await _get("https://api.dexscreener.com/token-profiles/latest/v1")


# ─── Meteora (via DexScreener — dlmm-api.meteora.ag is offline) ──────────────

_KNOWN_MINTS = {
    "SOL":  "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "JUP":  "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "WIF":  "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
}


async def meteora_pools(token: str = "SOL", limit: int = 15) -> list:
    """Meteora DLMM pools via DexScreener (Meteora's own API is offline). Sorted by TVL."""
    mint = _KNOWN_MINTS.get(token.upper(), token)
    is_address = len(mint) > 20
    if is_address:
        data = await _get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
    else:
        data = await _get("https://api.dexscreener.com/latest/dex/search", params={"q": token})
    pairs = data.get("pairs", []) if isinstance(data, dict) else []
    meteora = [p for p in pairs if p.get("dexId") == "meteora" and p.get("chainId") == "solana"]
    meteora.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
    return meteora[:limit]


# ─── Raydium ──────────────────────────────────────────────────────────────────

async def raydium_prices() -> dict:
    """Token prices from Raydium's AMM — mint address → USD price. Fast and comprehensive."""
    data = await _get("https://api.raydium.io/v2/main/price")
    return _cap(data)


async def raydium_clmm_pools(limit: int = 20) -> list:
    """Raydium Concentrated Liquidity (CLMM/V3) pool list — TVL, APR, fee tier, token pair."""
    data = await _get("https://api.raydium.io/v2/ammV3/ammPools")
    if isinstance(data, dict) and "data" in data:
        return data["data"][:limit]
    if isinstance(data, list):
        return data[:limit]
    return data


async def raydium_swap_quote(
    inputMint: str,
    outputMint: str,
    amount: int,
    slippageBps: int = 50,
    swapMode: str = "ExactIn",
    txVersion: str = "V0",
) -> dict:
    """Raydium swap quote via Trade API — price, route plan, price impact, threshold amount.
    swapMode: ExactIn (specify input amount) or ExactOut (specify desired output amount).
    amount: token amount in base units (lamports for SOL, micro-USDC for USDC etc.)
    slippageBps: slippage tolerance in basis points (50 = 0.5%, 100 = 1%).
    txVersion: V0 (versioned) or LEGACY.
    Returns: inputAmount, outputAmount, otherAmountThreshold, priceImpactPct, routePlan."""
    endpoint = "swap-base-in" if swapMode == "ExactIn" else "swap-base-out"
    data = await _get(
        f"https://transaction-v1.raydium.io/compute/{endpoint}",
        params={"inputMint": inputMint, "outputMint": outputMint, "amount": amount,
                "slippageBps": slippageBps, "txVersion": txVersion},
    )
    if isinstance(data, dict):
        if not data.get("success"):
            return {"error": data.get("msg", "unknown error"), "success": False}
        return data.get("data", data)
    return data


async def raydium_pools(
    poolType: str = "all",
    sortField: str = "volume24h",
    sortType: str = "desc",
    limit: int = 20,
) -> list:
    """Raydium pool list via API v3 — AMM, CLMM, CPMM pools with TVL, volume, fee rate, price.
    poolType: all | Standard (AMM v4) | Concentrated (CLMM) | AllFarm | StandardFarm | ConcentratedFarm.
    sortField: volume24h | tvl | fee24h | apr24h | liquidity.
    Returns top pools sorted by the chosen metric."""
    try:
        data = await _get(
            "https://api-v3.raydium.io/pools/info/list",
            params={"poolType": poolType, "poolSortField": sortField, "sortType": sortType,
                    "pageSize": min(limit, 100), "page": 1},
        )
    except Exception:
        # Raydium API v3 intermittent 500s — fall back to v2 CLMM endpoint
        return await raydium_clmm_pools(limit)
    if isinstance(data, dict) and "data" in data:
        inner = data["data"]
        pools = inner.get("data", inner) if isinstance(inner, dict) else inner
        return pools[:limit] if isinstance(pools, list) else pools
    return data


async def raydium_pool_search(ids: str) -> list:
    """Search Raydium pools by pool ID(s) — returns full pool info: TVL, price, volume, fee rate, tokens.
    ids: comma-separated pool ID(s). Use to look up a specific pool by address."""
    data = await _get("https://api-v3.raydium.io/pools/info/ids", params={"ids": ids})
    if isinstance(data, dict) and "data" in data:
        return data["data"] if isinstance(data["data"], list) else [data["data"]]
    return data


async def raydium_pool_by_lp(lps: str) -> list:
    """Raydium pool info by LP mint address(es) — reverse-lookup: LP token → pool details.
    lps: comma-separated LP mint address(es)."""
    data = await _get("https://api-v3.raydium.io/pools/info/lps", params={"lps": lps})
    if isinstance(data, dict) and "data" in data:
        return data["data"] if isinstance(data["data"], list) else [data["data"]]
    return data


async def raydium_pool_by_mint(
    mint1: str,
    mint2: str = None,
    poolType: str = "all",
    sortField: str = "volume24h",
    sortType: str = "desc",
    limit: int = 10,
) -> list:
    """Find Raydium pools containing a specific token (or token pair).
    mint1: required token mint. mint2: optional second token mint (narrows to pair).
    poolType: all | Standard | Concentrated | allFarm | standardFarm | concentratedFarm.
    sortField: volume24h | tvl | fee24h | apr24h | volume7d | apr7d.
    Returns pools that include the specified token(s), sorted by chosen metric."""
    try:
        data = await _get(
            "https://api-v3.raydium.io/pools/info/mint",
            params={k: v for k, v in {
                "mint1": mint1, "mint2": mint2, "poolType": poolType,
                "poolSortField": sortField, "sortType": sortType,
                "pageSize": min(limit, 100), "page": 1,
            }.items() if v is not None},
        )
    except Exception:
        return []
    if isinstance(data, dict) and "data" in data:
        inner = data["data"]
        pools = inner.get("data", inner) if isinstance(inner, dict) else inner
        return pools[:limit] if isinstance(pools, list) else pools
    return data


async def raydium_pool_keys(ids: str) -> list:
    """Raydium pool SDK keys by pool ID(s) — on-chain account addresses needed for transactions.
    ids: comma-separated pool IDs. Returns programId, vault addresses, config, rewardInfos."""
    data = await _get("https://api-v3.raydium.io/pools/key/ids", params={"ids": ids})
    if isinstance(data, dict) and "data" in data:
        return data["data"] if isinstance(data["data"], list) else [data["data"]]
    return data


async def raydium_pool_liquidity_history(id: str) -> dict:
    """Raydium pool TVL/liquidity history — time-series of pool TVL changes.
    id: pool ID. Returns array of {time (unix), liquidity (USD)} data points."""
    data = await _get("https://api-v3.raydium.io/pools/line/liquidity", params={"id": id})
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


async def raydium_pool_position_history(id: str) -> dict:
    """Raydium CLMM pool position history — price ticks and TVL over time.
    id: CLMM pool ID. Returns array of {time, price, tvl} data points."""
    data = await _get("https://api-v3.raydium.io/pools/line/position", params={"id": id})
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


# ─── Raydium Mint ─────────────────────────────────────────────────────────────

async def raydium_mint_list() -> dict:
    """Raydium default mint list — verified/standard tokens and block list.
    Returns: blockList (blocked mints), mintList (standard token metadata)."""
    data = await _get("https://api-v3.raydium.io/mint/list")
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


async def raydium_mint_info(mints: str) -> list:
    """Raydium token metadata by mint address(es) — symbol, name, decimals, logo, tags, extensions.
    mints: comma-separated mint addresses. Tags: hasFreeze, hasTransferFee."""
    data = await _get("https://api-v3.raydium.io/mint/ids", params={"mints": mints})
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


async def raydium_mint_price(mints: str = None) -> dict:
    """Raydium token prices from API v3 — mint → USD price string (more accurate than v2).
    mints: comma-separated mint addresses. Omit to use raydium_prices (v2) fallback."""
    if not mints:
        return await raydium_prices()
    data = await _get("https://api-v3.raydium.io/mint/price", params={"mints": mints})
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


# ─── Raydium Farms ────────────────────────────────────────────────────────────

async def raydium_farm_info(ids: str) -> list:
    """Raydium farm pool info by farm ID(s) — TVL, APR, LP price, reward tokens, tags.
    ids: comma-separated farm pool IDs."""
    data = await _get("https://api-v3.raydium.io/farms/info/ids", params={"ids": ids})
    if isinstance(data, dict) and "data" in data:
        return data["data"] if isinstance(data["data"], list) else [data["data"]]
    return data


async def raydium_farm_by_lp(lp: str, limit: int = 20) -> list:
    """Find Raydium farms by LP mint address — shows which farms accept this LP token.
    lp: LP token mint address. Returns farms with TVL, APR, reward info."""
    data = await _get(
        "https://api-v3.raydium.io/farms/info/lp",
        params={"lp": lp, "pageSize": min(limit, 100), "page": 1},
    )
    if isinstance(data, dict) and "data" in data:
        inner = data["data"]
        items = inner.get("data", inner) if isinstance(inner, dict) else inner
        return items[:limit] if isinstance(items, list) else items
    return data


async def raydium_farm_keys(ids: str) -> list:
    """Raydium farm SDK keys by farm ID(s) — on-chain account addresses for farm transactions.
    ids: comma-separated farm pool IDs."""
    data = await _get("https://api-v3.raydium.io/farms/key/ids", params={"ids": ids})
    if isinstance(data, dict) and "data" in data:
        return data["data"] if isinstance(data["data"], list) else [data["data"]]
    return data


# ─── Raydium IDO ──────────────────────────────────────────────────────────────

async def raydium_ido_keys(ids: str) -> list:
    """Raydium LaunchLab IDO pool keys by ID(s) — programId, authority, project/buy vault info.
    ids: comma-separated IDO pool IDs."""
    data = await _get("https://api-v3.raydium.io/ido/key/ids", params={"ids": ids})
    if isinstance(data, dict) and "data" in data:
        return data["data"] if isinstance(data["data"], list) else [data["data"]]
    return data


# ─── Raydium Main ─────────────────────────────────────────────────────────────

async def raydium_info() -> dict:
    """Raydium protocol stats — total TVL (USD) and 24-hour trading volume across all pools."""
    data = await _get("https://api-v3.raydium.io/main/info")
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


async def raydium_auto_fee() -> dict:
    """Raydium recommended transaction priority fees — medium (m), high (h), very high (vh) in micro-lamports.
    Use when building transactions to pick an appropriate priority fee tier."""
    data = await _get("https://api-v3.raydium.io/main/auto-fee")
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


async def raydium_clmm_config() -> list:
    """Raydium CLMM fee tier configurations — available concentrated liquidity fee tiers and tick spacings.
    Returns: id, tradeFeeRate, tickSpacing, protocolFeeRate for each tier."""
    data = await _get("https://api-v3.raydium.io/main/clmm-config")
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


async def raydium_cpmm_config() -> list:
    """Raydium CPMM fee tier configurations — available CPMM pool fee tiers and creation fees.
    Returns: id, tradeFeeRate, poolCreationFee for each tier."""
    data = await _get("https://api-v3.raydium.io/main/cpmm-config")
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


async def raydium_rpcs() -> dict:
    """Raydium recommended RPC endpoints for UI clients."""
    data = await _get("https://api-v3.raydium.io/main/rpcs")
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


async def raydium_chain_time() -> dict:
    """Raydium chain time offset — difference between local UTC and on-chain time in milliseconds."""
    data = await _get("https://api-v3.raydium.io/main/chain-time")
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


async def raydium_stake_pools() -> list:
    """Raydium RAY stake pool list — staking pools with TVL, APR, reward details."""
    data = await _get("https://api-v3.raydium.io/main/stake-pools")
    if isinstance(data, dict) and "data" in data:
        inner = data["data"]
        pools = inner.get("data", inner) if isinstance(inner, dict) else inner
        return pools if isinstance(pools, list) else inner
    return data


async def raydium_migrate_lp() -> list:
    """Raydium migrate LP pool list — AMM v4 pools eligible for migration to CLMM.
    Returns: ammId, lpMint, clmmId, farmIds, defaultPriceMin/Max."""
    data = await _get("https://api-v3.raydium.io/main/migrate-lp")
    if isinstance(data, dict) and "data" in data:
        return data["data"] if isinstance(data["data"], list) else data["data"]
    return data


async def raydium_version() -> dict:
    """Raydium API version — latest and minimum supported UI version."""
    data = await _get("https://api-v3.raydium.io/main/version")
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


# ─── Orca ─────────────────────────────────────────────────────────────────────

_ORCA_V2 = "https://api.orca.so/v2/solana"


def _orca_unwrap(resp: Any) -> Any:
    """Extract `data` list from Orca v2 paginated responses so _cap can truncate properly."""
    if isinstance(resp, dict) and "data" in resp and isinstance(resp["data"], list):
        return resp["data"]
    return resp


async def orca_pools(limit: int = 20, sort_by: str = None, sort_dir: str = None) -> list:
    """Orca Whirlpool (CLMM) pool list — token pair, price, TVL, APR, fee tier, tick spacing."""
    data = await _get(f"{_ORCA_V2}/pools", params={"limit": limit, "sortBy": sort_by, "sortDir": sort_dir})
    return _orca_unwrap(data)


async def orca_pools_search(query: str) -> list:
    """Search Orca pools by token symbol or address."""
    data = await _get(f"{_ORCA_V2}/pools/search", params={"query": query})
    return _orca_unwrap(data)


async def orca_pool_by_address(address: str) -> dict:
    """Detailed info for a specific Orca Whirlpool by its on-chain address."""
    data = await _get(f"{_ORCA_V2}/pools/{address}")
    return _orca_unwrap(data)


async def orca_locked_liquidity(address: str) -> Any:
    """Locked liquidity info for a specific Orca Whirlpool address."""
    data = await _get(f"{_ORCA_V2}/lock/{address}")
    return _orca_unwrap(data)


async def orca_protocol_stats() -> dict:
    """Orca protocol-level stats — TVL, 24h volume, fees, revenue."""
    return await _get(f"{_ORCA_V2}/protocol")


async def orca_protocol_token() -> dict:
    """ORCA token detailed info — price, market cap, supply, holders."""
    return await _get(f"{_ORCA_V2}/protocol/token")


async def orca_circulating_supply() -> Any:
    """ORCA token circulating supply."""
    return await _get(f"{_ORCA_V2}/protocol/token/circulating_supply")


async def orca_total_supply() -> Any:
    """ORCA token total minted supply."""
    return await _get(f"{_ORCA_V2}/protocol/token/total_supply")


async def orca_tokens(limit: int = 20, sort_by: str = None, sort_dir: str = None) -> list:
    """Paginated list of tokens available on Orca with metadata and pricing."""
    data = await _get(f"{_ORCA_V2}/tokens", params={"limit": limit, "sortBy": sort_by, "sortDir": sort_dir})
    return _orca_unwrap(data)


async def orca_tokens_search(query: str) -> list:
    """Search Orca tokens by name, symbol, or mint address."""
    data = await _get(f"{_ORCA_V2}/tokens/search", params={"query": query})
    return _orca_unwrap(data)


async def orca_token_by_mint(mint_address: str) -> dict:
    """Detailed info for a specific token on Orca by mint address."""
    data = await _get(f"{_ORCA_V2}/tokens/{mint_address}")
    return _orca_unwrap(data)


# ─── Kamino ───────────────────────────────────────────────────────────────────

async def kamino_strategies(limit: int = 20) -> list:
    """Kamino liquidity vault strategies — token pair, TVL, APY (7d/24h/30d), fee APR."""
    data = await _get(
        "https://api.kamino.finance/strategies/metrics",
        params={"env": "mainnet-beta", "status": "LIVE"},
    )
    if isinstance(data, list):
        return data[:limit]
    return data


async def kamino_lending() -> list:
    """Kamino lending market info — primary market address and description."""
    return await _get("https://api.kamino.finance/kamino-market")


_KAMINO_MAIN_MARKET = "7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF"


async def kamino_markets() -> list:
    """All Kamino lending markets — name, isPrimary, lendingMarket pubkey, description.
    Returns 29 markets including the main market."""
    return await _get("https://api.kamino.finance/v2/kamino-market")


async def kamino_market_reserves(market: str = _KAMINO_MAIN_MARKET) -> list:
    """Kamino lending reserves for a market — token symbol, borrowApy, supplyApy,
    totalSupply, totalBorrow, totalSupplyUsd, totalBorrowUsd, maxLtv.
    Defaults to the primary Kamino market (55 reserves)."""
    return await _get(f"https://api.kamino.finance/kamino-market/{market}/reserves/metrics")


async def kamino_oracle_prices() -> list:
    """Kamino oracle token prices — mint, name, price (USD), timestamp, maxAgeInSeconds.
    Returns prices for all 55 tokens supported in Kamino markets."""
    return await _get("https://api.kamino.finance/oracles/prices", params={"markets": "main"})


async def kamino_earn_vaults(limit: int = 30) -> list:
    """Kamino Earn vault list — address, tokenMint, shares, performance/management fees.
    Returns 121 vaults; capped at limit for conciseness."""
    data = await _get("https://api.kamino.finance/kvaults/vaults")
    if isinstance(data, list):
        return data[:limit]
    return data


async def kamino_staking_yields() -> list:
    """Kamino staking yields for LST tokens — tokenMint, apy.
    Returns ~41 LST staking APYs sourced from Kamino's oracle."""
    return await _get("https://api.kamino.finance/v2/staking-yields")


async def kamino_staking_yields_mean() -> list:
    """Mean (average) staking yield per LST token — tokenMint, apy."""
    return await _get("https://api.kamino.finance/v2/staking-yields/mean")


async def kamino_leverage_stats(market: str = _KAMINO_MAIN_MARKET) -> list:
    """Kamino leverage position stats per reserve pair — tvl, avgLeverage, totalBorrowed,
    totalBorrowedUsd, totalDeposited, totalDepositedUsd, totalObligations, depositReserve, borrowReserve."""
    return await _get(f"https://api.kamino.finance/kamino-market/{market}/leverage/metrics")


async def kamino_user_positions(wallet: str) -> list:
    """Kamino Earn vault positions for a wallet — vaultAddress, shares, tokenAmount, pnl."""
    return await _get(f"https://api.kamino.finance/kvaults/users/{wallet}/positions")


async def kamino_user_obligations(wallet: str, market: str = _KAMINO_MAIN_MARKET) -> list:
    """Kamino lending obligations for a wallet in a market — deposited collateral,
    borrowed amounts, health factor, LTV."""
    return await _get(
        f"https://api.kamino.finance/kamino-market/{market}/users/{wallet}/obligations",
        params={"env": "mainnet-beta"},
    )


async def kamino_epoch_info() -> dict:
    """Current Solana epoch info from Kamino — epoch number, firstSlot, lastSlot,
    startBlockTime, endBlockTime."""
    data = await _get("https://api.kamino.finance/epochs")
    if isinstance(data, list) and data:
        return data[0]
    return data



















# ─── Marinade ─────────────────────────────────────────────────────────────────

async def marinade_stats() -> dict:
    """Marinade Finance stats — mSOL/SOL and mSOL/USD exchange rates plus full TVL breakdown by staking type."""
    price_sol = await _get("https://api.marinade.finance/msol/price_sol")
    price_usd = await _get("https://api.marinade.finance/msol/price_usd")
    tlv       = await _get("https://api.marinade.finance/tlv")
    return {"msol_price_sol": price_sol, "msol_price_usd": price_usd, "tvl": tlv}


async def marinade_validators(
    limit: int = 50,
    epochs: int = None,
    query: str = None,
    query_identities: str = None,
    query_superminority: bool = None,
    query_marinade_score: bool = None,
    query_marinade_stake: bool = None,
    query_with_names: bool = None,
    order_field: str = None,
    order_direction: str = None,
    offset: int = None,
) -> dict:
    """Marinade validators with full filtering and sorting. order_field: Stake|Credits|MndeVotes, order_direction: ASC|DESC."""
    params = {
        "limit": limit,
        "epochs": epochs,
        "query": query,
        "query_identities": query_identities,
        "query_superminority": query_superminority,
        "query_marinade_score": query_marinade_score,
        "query_marinade_stake": query_marinade_stake,
        "query_with_names": query_with_names,
        "order_field": order_field,
        "order_direction": order_direction,
        "offset": offset,
    }
    data = await _get("https://validators-api.marinade.finance/validators", params=params)
    return data


async def marinade_validator_scores(limit: int = 100) -> dict:
    """Marinade validator scoring — vote_account, score, rank, component_scores, eligible_stake_msol for all ~1700 validators."""
    data = await _get("https://validators-api.marinade.finance/validators/scores")
    scores = data.get("scores", data) if isinstance(data, dict) else data
    if isinstance(scores, list):
        scores = scores[:limit]
    return {"scores": scores, "count": len(scores)}


async def marinade_score_breakdown(query_vote_account: str = None) -> dict:
    """Marinade detailed validator score breakdown. Pass query_vote_account to get breakdown for a specific validator."""
    params = {"query_vote_account": query_vote_account}
    return await _get("https://validators-api.marinade.finance/validators/score-breakdown", params=params)


async def marinade_validator_uptimes(identity: str) -> dict:
    """Uptime history for a specific Marinade validator. identity is the validator identity pubkey."""
    return await _get(f"https://validators-api.marinade.finance/validators/{identity}/uptimes")


async def marinade_validator_commissions(identity: str) -> dict:
    """Commission change history for a specific Marinade validator. identity is the validator identity pubkey."""
    return await _get(f"https://validators-api.marinade.finance/validators/{identity}/commissions")


async def marinade_validator_versions(identity: str) -> dict:
    """Validator client version history for a specific Marinade validator. identity is the validator identity pubkey."""
    return await _get(f"https://validators-api.marinade.finance/validators/{identity}/versions")


async def marinade_cluster_stats(epochs: int = None) -> dict:
    """Marinade cluster statistics — block production, datacenter concentration, current epoch info."""
    params = {"epochs": epochs}
    data = await _get("https://validators-api.marinade.finance/cluster-stats", params=params)
    # Trim to most recent epoch only; drop bulky stake arrays to stay within context limits
    if isinstance(data, dict) and "cluster_stats" in data:
        cs = data["cluster_stats"]
        if isinstance(cs.get("dc_concentration_stats"), list) and cs["dc_concentration_stats"]:
            latest = cs["dc_concentration_stats"][-1]
            cs["dc_concentration_stats"] = [{
                "epoch": latest.get("epoch"),
                "dc_concentration_by_aso": latest.get("dc_concentration_by_aso"),
                "dc_concentration_by_city": latest.get("dc_concentration_by_city"),
            }]
        if isinstance(cs.get("block_production_stats"), list):
            cs["block_production_stats"] = cs["block_production_stats"][-3:]
    return data


async def marinade_epoch_rewards() -> dict:
    """Marinade per-epoch rewards breakdown — MEV rewards, inflation estimate, Jito priority fees, block rewards."""
    return await _get("https://validators-api.marinade.finance/rewards")


async def marinade_staking_report() -> list:
    """Marinade next-epoch delegation plan — planned stake changes per validator (current_stake, next_stake, vote_account)."""
    data = await _get("https://validators-api.marinade.finance/reports/staking")
    planned = data.get("planned", data) if isinstance(data, dict) else data
    return planned[:60] if isinstance(planned, list) else planned


async def marinade_scoring_reports() -> dict:
    """Marinade scoring reports organized by epoch — scoring criteria, weights, and results per epoch."""
    return await _get("https://validators-api.marinade.finance/reports/scoring")


async def marinade_commission_changes() -> dict:
    """All validator commission change records tracked by Marinade — vote_account, old commission, new commission, epoch."""
    return await _get("https://validators-api.marinade.finance/reports/commission-changes")


async def marinade_config() -> dict:
    """Marinade system configuration — delegation authorities, scoring parameters, program addresses."""
    return await _get("https://validators-api.marinade.finance/static/config")


async def marinade_native_apy(limit: int = 20) -> dict:
    """Marinade native staking historical APY — per-epoch apy_5_epochs, apy_10_epochs, apy_all_epochs."""
    # Response is ~750KB (daily entries since genesis); use a large timeout and stream-parse
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.get("https://native-staking.marinade.finance/v1/apy")
        r.raise_for_status()
        data = r.json()
    days = data.get("days", data) if isinstance(data, dict) else data
    if isinstance(days, list):
        days = days[-limit:]  # take most recent entries
    return {"days": days, "count": len(days)}


async def marinade_msol_apy(period: str = "1y") -> dict:
    """mSOL APY for a given time period from Marinade. period: '7d', '30d', '1y' (default), '2y'.
    Returns value (decimal APY), end_time, end_price, start_time, start_price."""
    valid = {"7d", "30d", "1y", "2y"}
    if period not in valid:
        return {"error_type": "user_error", "message": f"Invalid period '{period}'. Valid options: 7d, 30d, 1y, 2y."}
    return await _get(f"https://api.marinade.finance/msol/apy/{period}")


async def marinade_jito_commissions(limit: int = 100) -> dict:
    """Marinade Jito validator commissions — vote_account, epoch, mev_commission_bps, priority_commission_bps."""
    data = await _get("https://validators-api.marinade.finance/jito")
    validators = data.get("validators", data) if isinstance(data, dict) else data
    if isinstance(validators, list):
        validators = validators[:limit]
    return {"validators": validators, "count": len(validators)}


async def marinade_msol_votes(top_n: int = 20) -> dict:
    """mSOL governance vote distribution — which validators mSOL holders are voting for.
    Aggregates 7000+ raw records into top validators by total mSOL vote weight."""
    data = await _get("https://snapshots-api.marinade.finance/v1/votes/msol/latest")
    records = data.get("records", [])
    tally: dict[str, dict] = {}
    for r in records:
        va = r.get("validatorVoteAccount", "")
        amt_str = r.get("amount") or "0"
        try:
            amt = float(amt_str)
        except (ValueError, TypeError):
            amt = 0.0
        if va not in tally:
            tally[va] = {"vote_account": va, "voter_count": 0, "total_msol": 0.0}
        tally[va]["voter_count"] += 1
        tally[va]["total_msol"] += amt
    ranked = sorted(tally.values(), key=lambda x: x["total_msol"], reverse=True)[:top_n]
    for entry in ranked:
        entry["total_msol"] = round(entry["total_msol"], 6)
    return {
        "top_validators": ranked,
        "total_voters": len(records),
        "snapshot_at": data.get("mSolSnapshotCreatedAt") or data.get("voteRecordsCreatedAt"),
        "note": f"Top {top_n} validators by mSOL vote weight out of {len(tally)} total",
    }


async def marinade_vemnde_votes(top_n: int = 20) -> dict:
    """veMNDE governance vote distribution — which validators veMNDE holders are voting for.
    Aggregates raw records into top validators by total veMNDE vote weight."""
    data = await _get("https://snapshots-api.marinade.finance/v1/votes/vemnde/latest")
    records = data.get("records", [])
    tally: dict[str, dict] = {}
    for r in records:
        va = r.get("validatorVoteAccount", "")
        amt_str = r.get("amount") or "0"
        try:
            amt = float(amt_str)
        except (ValueError, TypeError):
            amt = 0.0
        if va not in tally:
            tally[va] = {"vote_account": va, "voter_count": 0, "total_vemnde": 0.0}
        tally[va]["voter_count"] += 1
        tally[va]["total_vemnde"] += amt
    ranked = sorted(tally.values(), key=lambda x: x["total_vemnde"], reverse=True)[:top_n]
    for entry in ranked:
        entry["total_vemnde"] = round(entry["total_vemnde"], 6)
    return {
        "top_validators": ranked,
        "total_voters": len(records),
        "snapshot_at": data.get("veMNDESnapshotCreatedAt") or data.get("voteRecordsCreatedAt"),
        "note": f"Top {top_n} validators by veMNDE vote weight out of {len(tally)} total",
    }


async def marinade_wallet_msol_balance(wallet: str) -> dict:
    """Latest mSOL snapshot balance for a specific wallet from Marinade's snapshot system.
    Returns amount (in mSOL), slot, and snapshot timestamp."""
    if not wallet:
        return {"error_type": "user_error", "message": "wallet address is required"}
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(f"https://snapshots-api.marinade.finance/v1/snapshot/latest/msol/{wallet}")
    if r.status_code == 404:
        return {"wallet": wallet, "amount": "0", "note": "No mSOL found for this wallet in the latest snapshot"}
    r.raise_for_status()
    return r.json()


async def marinade_wallet_vemnde_balance(wallet: str) -> dict:
    """Latest veMNDE snapshot balance for a specific wallet from Marinade.
    Returns amount, slot, and snapshot timestamp."""
    if not wallet:
        return {"error_type": "user_error", "message": "wallet address is required"}
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(f"https://snapshots-api.marinade.finance/v1/snapshot/latest/vemnde/{wallet}")
    if r.status_code == 404:
        return {"wallet": wallet, "amount": "0", "note": "No veMNDE found for this wallet in the latest snapshot"}
    r.raise_for_status()
    return r.json()


async def marinade_wallet_native_stake(wallet: str, start_date: str, end_date: str) -> dict:
    """Native stake balance history for a specific wallet over a date range.
    start_date / end_date format: YYYY-MM-DD. Max range: 30 days to keep response size manageable.
    Returns list of {amount, slot, createdAt, snapshotCreatedAt} records."""
    if not wallet:
        return {"error_type": "user_error", "message": "wallet address is required"}
    if not start_date or not end_date:
        return {"error_type": "user_error", "message": "start_date and end_date are required (format: YYYY-MM-DD)"}
    from datetime import date as _date
    try:
        d0 = _date.fromisoformat(start_date)
        d1 = _date.fromisoformat(end_date)
    except ValueError:
        return {"error_type": "user_error", "message": "Invalid date format. Use YYYY-MM-DD."}
    if (d1 - d0).days > 30:
        return {"error_type": "user_error", "message": "Date range must be 30 days or less to avoid oversized responses."}
    if d1 < d0:
        return {"error_type": "user_error", "message": "end_date must be after start_date"}
    data = await _get(
        f"https://snapshots-api.marinade.finance/v1/stakers/ns/{wallet}",
        params={"startDate": start_date, "endDate": end_date},
    )
    return {"wallet": wallet, "start_date": start_date, "end_date": end_date, "records": data}


# ─── Solend / Save ────────────────────────────────────────────────────────────

async def solend_markets(ids: str = "", scope: str = "all", deployment: str = "production") -> list:
    """All Solend/Save lending market configs — reserves, tokens, pool addresses.
    ids: optional comma-separated market IDs to filter specific markets.
    scope: 'all' (default) or specific scope.
    deployment: 'production' (default) or 'devnet'."""
    params: dict = {"scope": scope, "deployment": deployment}
    if ids:
        params["ids"] = ids
    raw = await _get("https://api.solend.fi/v1/markets/configs", params=params)
    markets = raw if isinstance(raw, list) else raw.get("markets", [raw])
    slim = []
    for m in markets[:8]:
        reserves = [
            {
                "asset": r.get("asset") or r.get("symbol"),
                "mintAddress": r.get("mintAddress"),
                "supplyInterest": r.get("supplyInterest"),
                "borrowInterest": r.get("borrowInterest"),
            }
            for r in (m.get("reserves") or [])[:6]
        ]
        slim.append({"name": m.get("name"), "address": m.get("address"), "reserves": reserves})
    return slim


async def solend_reserves(scope: str = "all", ids: str = "") -> dict:
    """All Solend reserve data — supply APY, borrow APY, LTV, TVL, oracle price per reserve.
    scope: 'all' (default). ids: optional comma-separated reserve public keys to filter."""
    params: dict = {"scope": scope}
    if ids:
        params["ids"] = ids
    return await _get("https://api.solend.fi/v1/reserves", params=params)


async def solend_user_overview(wallet: str = "", obligation: str = "") -> dict:
    """Solend wallet/obligation overview — deposits, borrows, health factor across every market.
    Provide EITHER wallet (Solana wallet pubkey) OR obligation (obligation account pubkey), not both."""
    params: dict = {}
    if wallet:
        params["wallet"] = wallet
    elif obligation:
        params["obligation"] = obligation
    return await _get("https://api.solend.fi/v1/user-overview", params=params)


async def solend_stats() -> dict:
    """Solend protocol-wide stats — totalDepositsUSD, totalBorrowsUSD, totalMarkets, totalObligations, uniqueAssets, totalFeesUSD."""
    return await _get("https://api.solend.fi/v1/stats")


async def solend_daily_fees(ts: int, span: str, dumpy_only: bool = False) -> dict:
    """Solend fee stats for a time period.
    ts: Unix timestamp (start of period). span: time window e.g. '1d', '7d', '30d'.
    Returns hostOriginationFees, protocolOriginationFees, protocolFlashLoanFees, protocolSpreadFees."""
    return await _get(
        "https://api.solend.fi/v1/stats/daily-fees",
        params={"ts": ts, "span": span, "dumpy-only": str(dumpy_only).lower()},
    )


async def solend_lst_rates() -> dict:
    """Solend LST (Liquid Staking Token) APR/APY rates — jitoSOL, mSOL, bSOL, sSOL and others.
    Keys are LST mint addresses; values contain apr and apy fields."""
    return await _get("https://api.solend.fi/v1/lst-rates")


async def solend_prices(mints: str = "", symbols: str = "") -> dict:
    """Solend oracle prices for one or more tokens.
    mints: comma-separated mint addresses. symbols: comma-separated symbols e.g. 'SOL,USDC'.
    At least one of mints or symbols must be provided."""
    params: dict = {}
    if mints:
        params["mints"] = mints
    if symbols:
        params["symbols"] = symbols
    return await _get("https://api.solend.fi/v1/prices", params=params)


async def solend_historical_prices(mint: str, timestamps: str) -> dict:
    """Solend historical oracle price for a token at specific Unix timestamps.
    mint: token mint address. timestamps: comma-separated Unix timestamps e.g. '1700000000,1700100000'.
    Returns map of timestamp → price."""
    return await _get(
        "https://api.solend.fi/v1/historical-prices",
        params={"mint": mint, "timestamps": timestamps},
    )


async def solend_reserves_history(ids: str, span: str = "1w") -> dict:
    """Historical supply/borrow APY for Solend reserves over time.
    ids: comma-separated reserve public keys (get from solend_markets).
    span: '1w' (default), '30d', '90d', or '1y'.
    Returns map of reserveID → [{supplyAPY, borrowAPY, supplyAPR, borrowAPR, timestamp, cTokenExchangeRate}]."""
    return await _get(
        "https://api.solend.fi/v1/reserves/historical-interest-rates",
        params={"ids": ids, "span": span},
    )


async def solend_reserves_config_changes(reserve_ids: str, market_id: str) -> dict:
    """Solend reserve configuration change history — LTV changes, liquidation threshold changes, etc.
    reserve_ids: comma-separated reserve public keys. market_id: lending market public key."""
    return await _get(
        "https://api.solend.fi/v1/reserves/config-changes",
        params={"reserveIDs": reserve_ids, "marketID": market_id},
    )


async def solend_daily_stats(date: str = "") -> dict:
    """Solend daily protocol stats for a specific date (or today if not specified).
    date: YYYY-MM-DD format. Returns totalBorrowers, totalDepositors, outstandingBorrowsUSD, interestGeneratedUSD."""
    d = date.strip() if date.strip() else str(_date.today())
    return await _get("https://api.solend.fi/v1/daily-stats", params={"date": d})


async def solend_circulating_supply() -> dict:
    """Solend/SAVE token circulating supply."""
    return await _get("https://api.solend.fi/v1/circulating-supply")


async def solend_total_supply() -> dict:
    """Solend/SAVE token total supply."""
    return await _get("https://api.solend.fi/v1/total-supply")


async def solend_save_metrics() -> dict:
    """SAVE token metrics — totalFees, annualizedRevenue, savePrice."""
    return await _get("https://api.solend.fi/v1/save/metrics")


async def solend_save_price_chart(period: str) -> dict:
    """SAVE token price chart data. period: '1d', '7d', '30d', '90d', '1y', or 'all'."""
    return await _get("https://api.solend.fi/v1/save/charts/price", params={"period": period})


async def solend_save_revenue_chart(period: str) -> dict:
    """SAVE protocol revenue chart data. period: '1d', '7d', '30d', '90d', '1y', or 'all'."""
    return await _get("https://api.solend.fi/v1/save/charts/revenue", params={"period": period})


async def solend_isolated_pool_stats() -> dict:
    """Solend isolated pool statistics — per-pool TVL, borrow, utilization for all isolated lending pools."""
    return await _get("https://api.solend.fi/v1/isolated/pool-stats")


async def solend_announcements(id: str = "") -> dict:
    """Solend market announcements. id: market address (optional — defaults to main market)."""
    mid = id.strip() if id.strip() else "4UpD2fh7xH3VP9QQaXtsS1YY3bxzWhtfpks7FatyKvdY"
    return await _get("https://api.solend.fi/v1/markets/announcements", params={"id": mid})


async def solend_changelogs(id: str = "") -> dict:
    """Solend market changelogs — protocol parameter changes, upgrades. id: market address (optional — defaults to main market)."""
    mid = id.strip() if id.strip() else "4UpD2fh7xH3VP9QQaXtsS1YY3bxzWhtfpks7FatyKvdY"
    return await _get("https://api.solend.fi/v1/markets/changelogs", params={"id": mid})


async def solend_reward_stats(flat: bool = False) -> dict:
    """Solend liquidity mining reward emission stats — reward APYs and emission rates per reserve across all markets.
    flat: if True, returns a flat list instead of nested by market."""
    return await _get(
        "https://api.solend.fi/liquidity-mining/external-reward-stats-v2",
        params={"flat": str(flat).lower()},
    )


async def solend_reward_score(wallet: str) -> dict:
    """Solend liquidity mining reward score for a wallet — used for reward distribution calculations."""
    return await _get(
        "https://api.solend.fi/liquidity-mining/external-reward-score-v2",
        params={"wallet": wallet},
    )


async def solend_reward_proofs(obligation: str) -> dict:
    """Solend liquidity mining reward Merkle proofs for an obligation — needed for on-chain reward claims."""
    return await _get(
        "https://api.solend.fi/liquidity-mining/reward-proofs",
        params={"obligation": obligation},
    )


async def solend_additional_emissions() -> list:
    """Solend additional emission rates — supplemental reward APYs per reserve beyond base rewards."""
    return await _get("https://api.solend.fi/liquidity-mining/additional-emissions")


async def solend_confirmed_rewards(wallet: str) -> dict:
    """Confirmed (claimable) Solend liquidity mining rewards for a wallet. Returns {results, next}."""
    return await _get(
        "https://api.solend.fi/liquidity-mining/confirmed-rewards",
        params={"wallet": wallet},
    )


async def solend_history(obligation: str) -> dict:
    """Transaction history for a Solend obligation account — deposits, withdrawals, borrows, repays.
    obligation: the obligation account public key (not wallet address)."""
    return await _get("https://api.solend.fi/history", params={"obligation": obligation})


async def solend_history_v2(obligation: str, max_slot: int = 0) -> dict:
    """Solend v2 transaction history for an obligation account with slot-based pagination.
    obligation: obligation account pubkey. max_slot: return transactions before this slot (0 = latest)."""
    params: dict = {"obligation": obligation}
    if max_slot:
        params["maxSlot"] = max_slot
    return await _get("https://api.solend.fi/history-v2", params=params)


async def solend_ctoken_history(wallet: str) -> list:
    """cToken (collateral token) deposit/withdraw history for a wallet across all Solend markets."""
    return await _get("https://api.solend.fi/history-v2/ctoken-history", params={"wallet": wallet})


async def solend_liquidation_attempts(market: str) -> dict:
    """Solend liquidation attempt history for a specific lending market.
    market: lending market public key (get from solend_markets)."""
    return await _get(
        "https://api.solend.fi/history-v2/liquidation-attempts",
        params={"market": market},
    )


async def solend_margin_trading_history(
    obligation: str = "", obligations: str = "", wallet: str = "", max_slot: int = 0
) -> dict:
    """Solend margin trading transaction history.
    obligation: single obligation pubkey. obligations: comma-separated obligation pubkeys.
    wallet: wallet pubkey. max_slot: pagination cursor (slot number)."""
    params: dict = {}
    if obligation:
        params["obligation"] = obligation
    if obligations:
        params["obligations"] = obligations
    if wallet:
        params["wallet"] = wallet
    if max_slot:
        params["maxSlot"] = max_slot
    return await _get("https://api.solend.fi/history-v2/margin-trading", params=params)


async def solend_snapshot(wallet: str, ts: int) -> dict:
    """Solend portfolio snapshot at a specific Unix timestamp.
    wallet: wallet pubkey. ts: Unix timestamp for the snapshot point."""
    return await _get(
        "https://api.solend.fi/history-v2/snapshot",
        params={"wallet": wallet, "ts": ts},
    )


async def solend_transactions(signatures: str) -> dict:
    """Solend transaction details by signature(s).
    signatures: comma-separated transaction signatures."""
    return await _get("https://api.solend.fi/transactions", params={"signatures": signatures})


async def solend_margin_trading_transactions(signatures: str) -> dict:
    """Solend margin trading transaction details by signature(s).
    signatures: comma-separated transaction signatures."""
    return await _get("https://api.solend.fi/v1/transactions/margin-trading", params={"signatures": signatures})


async def solend_transaction_notes(signatures: str) -> dict:
    """Solend human-readable transaction notes/labels for given signatures.
    signatures: comma-separated transaction signatures."""
    return await _get("https://api.solend.fi/transaction-notes", params={"signatures": signatures})


async def solend_vip_eligibility(wallet: str) -> dict:
    """Check if a wallet is eligible for Solend VIP status. Returns {eligible, verified}."""
    return await _get("https://api.solend.fi/vip/eligibility", params={"wallet": wallet})


async def solend_airdrops(wallet: str) -> list:
    """Solend airdrop information for a wallet — eligible amounts and claim status."""
    return await _get("https://api.solend.fi/v1/airdrops", params={"wallet": wallet})


async def solend_airdrops_jito(wallet: str) -> list:
    """Jito-specific airdrop information tracked by Solend for a wallet."""
    return await _get("https://api.solend.fi/v1/airdrops/jito", params={"wallet": wallet})


async def solend_obligations_filtered(
    min_deposit_usd: str = "0",
    min_borrow_usd: str = "0",
    min_utilization: str = "0",
    deposit_assets: str = "",
    borrow_assets: str = "",
    market_address: str = "",
    include_without_borrows: str = "false",
    limit: str = "20",
    offset: str = "0",
) -> dict:
    """Search Solend obligations by filter criteria — useful for finding at-risk positions.
    min_deposit_usd: minimum deposit value in USD. min_borrow_usd: minimum borrow value.
    min_utilization: minimum utilization ratio (0-1). deposit_assets/borrow_assets: comma-separated mints.
    market_address: filter by specific lending market. limit/offset: pagination."""
    params = {
        "minDepositValueUsd": min_deposit_usd,
        "minBorrowValueUsd": min_borrow_usd,
        "minUtilization": min_utilization,
        "depositAssets": deposit_assets,
        "borrowAssets": borrow_assets,
        "marketAddress": market_address,
        "includeObligationsWithoutBorrows": include_without_borrows,
        "limit": limit,
        "offset": offset,
    }
    return await _post("https://api.solend.fi/obligations/filtered", {}, params=params)


async def solend_squeezy_obligations(borrow_mint: str) -> dict:
    """Solend 'squeezy' obligations — positions at high liquidation risk for a given borrow asset.
    borrow_mint: mint address of the borrowed token (e.g., USDC mint)."""
    return await _get("https://api.solend.fi/squeezy/obligations", params={"borrowMint": borrow_mint})


async def solend_notifications(wallet: str) -> dict:
    """Solend in-app notifications for a wallet — protocol alerts, liquidation warnings, reward notices.
    wallet: Solana wallet public key (base58)."""
    return await _get("https://api.solend.fi/v1/notifications", params={"wallet": wallet})


async def solend_tokens_all(deployment: str = "production") -> list:
    """All tokens listed on Solend by deployment environment.
    deployment: 'production' (default) or 'devnet'."""
    return await _get("https://api.solend.fi/tokens/all", params={"deployment": deployment})


async def solend_tokens(deployment: str = "production", mints: str = "", symbols: str = "") -> dict:
    """Solend token info filtered by mint addresses or symbols.
    deployment: 'production' (default) or 'devnet'.
    mints: comma-separated mint addresses. symbols: comma-separated token symbols e.g. 'SOL,USDC'."""
    params: dict = {"deployment": deployment}
    if mints:
        params["mints"] = mints
    if symbols:
        params["symbols"] = symbols
    return await _get("https://api.solend.fi/tokens", params=params)


async def solend_referral_payments() -> dict:
    """Solend referral payments overview — total referral fees and payment history."""
    return await _get("https://api.solend.fi/referrals/payments")


async def solend_referral_attributed_payments(wallet: str) -> dict:
    """Solend referral payments attributed to a specific wallet — fees earned as a referrer.
    wallet: Solana wallet public key (base58)."""
    return await _get("https://api.solend.fi/referrals/attributed-payments", params={"wallet": wallet})


async def solend_referral_referrer(referred_wallet: str) -> dict:
    """Look up the referrer for a given wallet on Solend.
    referred_wallet: the wallet whose referrer you want to find (base58)."""
    return await _get("https://api.solend.fi/referrals/referrer", params={"referredWallet": referred_wallet})


async def solend_referral_referred(referrer_wallet: str) -> dict:
    """List wallets referred by a given referrer on Solend.
    referrer_wallet: the referrer wallet public key (base58)."""
    return await _get("https://api.solend.fi/referrals/referred", params={"referrerWallet": referrer_wallet})


async def solend_referral_stats() -> dict:
    """Solend referral program aggregate stats — total referrers, total referred wallets, total fees paid."""
    return await _get("https://api.solend.fi/referrals/stats")


async def solend_points() -> dict:
    """Solend points overview — current season, multipliers, distribution info."""
    return await _get("https://api.solend.fi/points")


async def solend_points_leaderboard() -> dict:
    """Solend points leaderboard — top wallets by points earned."""
    return await _get("https://api.solend.fi/points/leaderboard")


async def solend_points_config() -> dict:
    """Solend points system configuration — rules, weights, season details."""
    return await _get("https://api.solend.fi/points/config")


async def solend_points_total() -> dict:
    """Total points issued across all Solend users."""
    return await _get("https://api.solend.fi/points/total")


async def solend_points_adjustments() -> dict:
    """Solend manual points adjustments — admin-issued bonus or penalty entries."""
    return await _get("https://api.solend.fi/points/adjustments")


# ─── Jito (via gateway — confirmed working) ───────────────────────────────────

async def jito_stake_pool_stats() -> dict:
    """JitoSOL stake pool stats time series — TVL, APY, supply, validator count by epoch/date."""
    return await _post(f"{KOBE}/api/v1/stake_pool_stats", {})


async def jito_jitosol_ratio() -> list:
    """jitoSOL/SOL exchange rate history — shows staking reward accumulation over time."""
    data = await _post(f"{KOBE}/api/v1/jitosol_sol_ratio", {})
    ratios = data.get("ratios", data) if isinstance(data, dict) else data
    return ratios


async def jito_validators(limit: int = 20) -> list:
    """Jito validators with MEV commission rates, vote account, identity, and MEV rewards earned."""
    data = await _post(f"{KOBE}/api/v1/validators", {"limit": limit})
    validators = data.get("validators", data) if isinstance(data, dict) else data
    return validators[:limit] if isinstance(validators, list) else validators


async def jito_preferred_validators(limit: int = 10) -> list:
    """Top validators recommended for jitoSOL withdrawal — ranked by withdrawable stake."""
    return await _get(f"{KOBE}/api/v1/preferred_withdraw_validator_list", params={"limit": limit})


# ─── Jito (tip-floor via gateway for CORS; rest direct to Kobe — all public) ──

async def jito_tip_floor(jwt: str) -> list:
    """Jito MEV tip floor percentiles (P25–P99) + EMA. Tells you how much SOL tip to include."""
    return await _get(f"{GATEWAY}/market/jito/tip-floor", headers=_auth(jwt))


async def jito_mev_rewards() -> dict:
    """Current epoch MEV reward totals and per-lamport reward rate for jitoSOL stakers."""
    return await _post(f"{KOBE}/api/v1/mev_rewards", {})


async def jito_daily_mev(days: int = 30) -> list:
    """Daily MEV rewards for the last N days — tip count, SOL tipped, unique tippers, validator tips."""
    data = await _get(f"{KOBE}/api/v1/daily_mev_rewards")
    items = data if isinstance(data, list) else data.get("data", [])
    return items[-days:]


async def jito_stake_growth(epochs: int = 30) -> dict:
    """jitoSOL stake ratio growth over time (epoch → ratio). Shows Jito adoption trend."""
    data = await _get(f"{KOBE}/api/v1/jito_stake_over_time")
    ratio_map = data.get("stake_ratio_over_time", data) if isinstance(data, dict) else data
    if isinstance(ratio_map, dict):
        recent = dict(sorted(ratio_map.items(), key=lambda x: int(x[0]))[-epochs:])
        return {"stake_ratio_over_time": recent}
    return data


async def jito_mev_commission(epochs: int = 30) -> dict:
    """Average MEV commission taken by Jito validators per epoch — affects staker net yield."""
    data = await _post(f"{KOBE}/api/v1/mev_commission_average_over_time", {})
    comm_map = data.get("average_mev_commission_over_time", data) if isinstance(data, dict) else data
    if isinstance(comm_map, dict):
        recent = dict(sorted(comm_map.items(), key=lambda x: int(x[0]))[-epochs:])
        return {"average_mev_commission_over_time": recent}
    return data


# ─── Helius RPC ───────────────────────────────────────────────────────────────

async def helius_token_holders(mint: str) -> list:
    """Top 20 token accounts (holders) for any SPL token via Helius RPC getTokenLargestAccounts."""
    result = await _post(HELIUS, {
        "jsonrpc": "2.0", "id": 1,
        "method": "getTokenLargestAccounts",
        "params": [mint],
    })
    return result.get("result", {}).get("value", result)


async def helius_token_supply(mint: str) -> dict:
    """Total supply and decimals for a token via Helius getTokenSupply."""
    result = await _post(HELIUS, {
        "jsonrpc": "2.0", "id": 1,
        "method": "getTokenSupply",
        "params": [mint],
    })
    return result.get("result", {}).get("value", result)


async def helius_wallet_tokens(wallet: str) -> dict:
    """All SPL token accounts owned by a wallet via Helius getTokenAccountsByOwner."""
    result = await _post(HELIUS_RPC, {
        "jsonrpc": "2.0", "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [wallet, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"}, {"encoding": "jsonParsed"}],
    })
    return result.get("result", {}).get("value", result)


async def helius_wallet_txs(wallet: str, limit: int = 20) -> list:
    """Wallet transaction history via Helius — decoded type (SWAP, TRANSFER, STAKE), source protocol, description, SOL value."""
    if not HELIUS_KEY:
        return {"error": "HELIUS_API_KEY not configured"}
    return await _get(
        f"{HELIUS_API}/addresses/{wallet}/transactions",
        params={"limit": min(limit, 50), "api-key": HELIUS_KEY},
    )


# ─── Security (gateway — rugcheck) ────────────────────────────────────────────

async def token_security(mint: str, jwt: str) -> dict:
    """Token security/risk check — mint authority, freeze authority, LP lock %, risk score."""
    return await _get(f"{GATEWAY}/market/security/{mint}", headers=_auth(jwt))


# ─── User portfolio (gateway — requires JWT) ──────────────────────────────────

async def wallet_balance(jwt: str) -> dict:
    """Authenticated user's wallet balance — SOL + all SPL tokens."""
    return await _get(f"{GATEWAY}/balance", headers=_auth(jwt))


async def user_transactions(jwt: str) -> dict:
    """Authenticated user's OPRAI transaction history."""
    return await _get(f"{GATEWAY}/transactions", headers=_auth(jwt))


async def limit_orders(jwt: str, status: str = "open") -> dict:
    """User's Jupiter limit orders. status: open | closed."""
    return await _get(f"{GATEWAY}/actions/limit-orders", params={"status": status}, headers=_auth(jwt))


async def dca_orders(jwt: str, status: str = "active") -> dict:
    """User's Jupiter DCA orders. status: active | cancelled."""
    return await _get(f"{GATEWAY}/actions/dca-orders", params={"status": status}, headers=_auth(jwt))


# ─── Birdeye ──────────────────────────────────────────────────────────────────

def _birdeye_headers(chain: str = "solana") -> dict:
    if not BIRDEYE_KEY:
        raise ValueError("BIRDEYE_API_KEY not configured — add it to .env")
    return {"X-API-KEY": BIRDEYE_KEY, "x-chain": chain}


async def birdeye_price(address: str, check_liquidity: bool = False) -> dict:
    """Real-time price for a single token from Birdeye."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/price",
        params={"address": address, "check_liquidity": check_liquidity},
        headers=_birdeye_headers(),
    )


async def birdeye_multi_price(
    list_address: str,
    check_liquidity: int = None,
    include_liquidity: bool = None,
    ui_amount_mode: str = None,
    chain: str = "solana",
) -> dict:
    """Real-time prices for multiple tokens (comma-separated mint addresses)."""
    params = {"list_address": list_address}
    if check_liquidity is not None:
        params["check_liquidity"] = check_liquidity
    if include_liquidity is not None:
        params["include_liquidity"] = include_liquidity
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/multi_price",
        params=params,
        headers=_birdeye_headers(chain=chain),
    )


async def birdeye_ohlcv(
    address: str,
    type: str = "1D",
    time_from: int = None,
    time_to: int = None,
    currency: str = "usd",
    ui_amount_mode: str = None,
    mode: str = None,
    count_limit: int = None,
    padding: bool = None,
    outlier: bool = None,
    chain: str = "solana",
) -> dict:
    """OHLCV candlestick data for a token via Birdeye v3.
    type: 1s 15s 30s 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M
    currency: usd or native
    mode: range (default, needs time_from+time_to) or count (needs only one of time_from/time_to + count_limit)
    """
    params: dict = {"address": address, "type": type}
    if time_from is not None:
        params["time_from"] = time_from
    if time_to is not None:
        params["time_to"] = time_to
    if currency:
        params["currency"] = currency
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    if mode is not None:
        params["mode"] = mode
    if count_limit is not None:
        params["count_limit"] = count_limit
    if padding is not None:
        params["padding"] = padding
    if outlier is not None:
        params["outlier"] = outlier
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/ohlcv",
        params=params,
        headers=_birdeye_headers(chain=chain),
    )


async def birdeye_ohlcv_pair(
    address: str,
    type: str = "1D",
    time_from: int = None,
    time_to: int = None,
    mode: str = None,
    count_limit: int = None,
    padding: bool = None,
    outlier: bool = None,
    inversion: bool = None,
    chain: str = "solana",
) -> dict:
    """OHLCV candlestick data for a specific DEX trading pair via Birdeye v3.
    address: pair/pool contract address.
    inversion=true: invert base/quote (e.g. USDC per SOL instead of SOL per USDC).
    mode=range (default): needs time_from + time_to.
    mode=count: needs count_limit + one of time_from/time_to.
    """
    params: dict = {"address": address, "type": type}
    if time_from is not None:
        params["time_from"] = time_from
    if time_to is not None:
        params["time_to"] = time_to
    if mode is not None:
        params["mode"] = mode
    if count_limit is not None:
        params["count_limit"] = count_limit
    if padding is not None:
        params["padding"] = padding
    if outlier is not None:
        params["outlier"] = outlier
    if inversion is not None:
        params["inversion"] = inversion
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/ohlcv/pair",
        params=params,
        headers=_birdeye_headers(chain=chain),
    )


async def birdeye_ohlcv_v1(
    address: str,
    type: str = "1D",
    time_from: int = None,
    time_to: int = None,
    currency: str = "usd",
    ui_amount_mode: str = None,
    chain: str = "solana",
) -> dict:
    """Legacy (v1) token OHLCV from Birdeye /defi/ohlcv.
    Simpler than v3 (no mode/count/padding/outlier) but supports 16+ chains.
    type: 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M (no sub-minute)
    currency: usd or native
    Extra chains vs v3: arbitrum, avalanche, optimism, polygon, zksync, etc.
    """
    params: dict = {"address": address, "type": type}
    if time_from is not None:
        params["time_from"] = time_from
    if time_to is not None:
        params["time_to"] = time_to
    if currency:
        params["currency"] = currency
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/ohlcv",
        params=params,
        headers=_birdeye_headers(chain=chain),
    )


async def birdeye_ohlcv_pair_v1(
    address: str,
    type: str = "1D",
    time_from: int = None,
    time_to: int = None,
    chain: str = "solana",
) -> dict:
    """Legacy (v1) pair OHLCV from Birdeye /defi/ohlcv/pair.
    Takes a DEX pair/pool address (not a token address).
    Simpler than v3 ohlcv_pair (no mode/count/padding/outlier/inversion) but supports 16+ chains.
    type: 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M
    time_from and time_to are required.
    """
    params: dict = {"address": address, "type": type}
    if time_from is not None:
        params["time_from"] = time_from
    if time_to is not None:
        params["time_to"] = time_to
    return await _get(
        f"{BIRDEYE_BASE}/defi/ohlcv/pair",
        params=params,
        headers=_birdeye_headers(chain=chain),
    )


async def birdeye_ohlcv_base_quote(
    base_address: str,
    quote_address: str,
    type: str = "1D",
    time_from: int = None,
    time_to: int = None,
    ui_amount_mode: str = None,
    chain: str = "solana",
) -> dict:
    """OHLCV data for a pair defined by explicit base and quote mint addresses.
    type: 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M
    ui_amount_mode: raw (default), scaled, both — Solana only
    time_from and time_to are required for range queries.
    """
    params: dict = {
        "base_address": base_address,
        "quote_address": quote_address,
        "type": type,
    }
    if time_from is not None:
        params["time_from"] = time_from
    if time_to is not None:
        params["time_to"] = time_to
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/ohlcv/base_quote",
        params=params,
        headers=_birdeye_headers(chain=chain),
    )


async def birdeye_history_price(
    address: str,
    address_type: str = "token",
    type: str = "1D",
    time_from: int = None,
    time_to: int = None,
    ui_amount_mode: str = None,
    chain: str = "solana",
) -> dict:
    """Historical close-price timeline for a token or pair from Birdeye.
    address_type: token (default) or pair
    type: 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M
    ui_amount_mode: raw (default), scaled, both — Solana only
    time_from and time_to are required.
    """
    params: dict = {
        "address": address,
        "address_type": address_type,
        "type": type,
    }
    if time_from is not None:
        params["time_from"] = time_from
    if time_to is not None:
        params["time_to"] = time_to
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/history_price",
        params=params,
        headers=_birdeye_headers(chain=chain),
    )


async def birdeye_price_at_time(
    address: str,
    unixtime: int = None,
    ui_amount_mode: str = None,
) -> dict:
    """Price of a token at a specific Unix timestamp from Birdeye. Solana only.
    unixtime: optional — omit for current price
    ui_amount_mode: raw (default), scaled, both
    """
    params: dict = {"address": address}
    if unixtime is not None:
        params["unixtime"] = unixtime
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/historical_price_unix",
        params=params,
        headers=_birdeye_headers(),
    )


async def birdeye_price_volume(
    address: str,
    type: str = "24h",
    ui_amount_mode: str = None,
) -> dict:
    """Price and trading volume stats for a single token from Birdeye /defi/price_volume/single.
    type: 1h, 2h, 4h, 8h, 24h (default)
    ui_amount_mode: raw (default), scaled, both
    """
    params: dict = {"address": address, "type": type}
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/price_volume/single",
        params=params,
        headers=_birdeye_headers(),
    )


async def birdeye_price_volume_multi(list_address: str, type: str = "24h") -> dict:
    """Price and trading volume stats for multiple tokens (comma-separated addresses).
    type: 24h, 1h, 7d, 30d
    """
    addresses = [a.strip() for a in list_address.split(",") if a.strip()]
    return await _post(
        f"{BIRDEYE_BASE}/defi/price_volume/multi",
        body={"list_address": addresses, "type": type},
        headers=_birdeye_headers(),
    )


async def birdeye_token_overview(
    address: str,
    frames: str = None,
    ui_amount_mode: str = None,
    chain: str = "solana",
) -> dict:
    """Comprehensive token overview from Birdeye — price, volume, liquidity, market stats.
    frames: comma-separated custom intervals e.g. '1h,4h,24h' (up to 8, supports 1m-1440m, 5s-3600s, 1h 2h 4h 8h 24h)
    ui_amount_mode: raw or scaled (Solana only, default: scaled)
    Supports 16+ chains via chain param.
    """
    params: dict = {"address": address}
    if frames is not None:
        params["frames"] = frames
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/token_overview",
        params=params,
        headers=_birdeye_headers(chain=chain),
    )


async def birdeye_token_metadata(address: str, chain: str = "solana") -> dict:
    """Token metadata from Birdeye v3 — name, symbol, decimals, logo, extensions.
    Supports 16+ chains via chain param.
    """
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/meta-data/single",
        params={"address": address},
        headers=_birdeye_headers(chain=chain),
    )


async def birdeye_token_metadata_multi(list_address: str, chain: str = "solana") -> dict:
    """Token metadata for multiple tokens from Birdeye v3 (max 50).
    Supports 16+ chains via chain param.
    """
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/meta-data/multiple",
        params={"list_address": list_address},
        headers=_birdeye_headers(chain=chain),
    )


async def birdeye_token_market_data(
    address: str,
    ui_amount_mode: str = None,
    chain: str = "solana",
) -> dict:
    """Token market data from Birdeye v3 — price, market cap, FDV, liquidity, supply.
    ui_amount_mode: raw or scaled (default: scaled, Solana only)
    Supports 16+ chains via chain param.
    """
    params: dict = {"address": address}
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/market-data",
        params=params,
        headers=_birdeye_headers(chain=chain),
    )


async def birdeye_token_market_data_multi(
    list_address: str,
    ui_amount_mode: str = None,
    chain: str = "solana",
) -> dict:
    """Token market data for multiple tokens from Birdeye v3 (max 20).
    ui_amount_mode: raw or scaled (default: scaled)
    Supports 16+ chains via chain param.
    """
    params: dict = {"list_address": list_address}
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/market-data/multiple",
        params=params,
        headers=_birdeye_headers(chain=chain),
    )


async def birdeye_token_trade_data(
    address: str,
    frames: str = None,
    ui_amount_mode: str = None,
) -> dict:
    """Token trade data (buy/sell counts, volume) across multiple time frames from Birdeye v3.
    frames: comma-separated intervals e.g. '1h,4h,24h' (up to 8)
    ui_amount_mode: raw or scaled (default: scaled)
    """
    params: dict = {"address": address}
    if frames is not None:
        params["frames"] = frames
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/trade-data/single",
        params=params,
        headers=_birdeye_headers(),
    )


async def birdeye_token_trade_data_multi(
    list_address: str,
    frames: str = None,
    ui_amount_mode: str = None,
) -> dict:
    """Token trade data for multiple tokens from Birdeye v3.
    frames: comma-separated intervals (up to 8)
    ui_amount_mode: raw or scaled (default: scaled)
    """
    params: dict = {"list_address": list_address}
    if frames is not None:
        params["frames"] = frames
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/trade-data/multiple",
        params=params,
        headers=_birdeye_headers(),
    )


async def birdeye_exit_liquidity(address: str) -> dict:
    """Exit liquidity in USD and native tokens for a Base-chain token from Birdeye v3.
    NOTE: This endpoint only supports the Base (Ethereum L2) network.
    """
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/exit-liquidity",
        params={"address": address},
        headers=_birdeye_headers(chain="base"),
    )


async def birdeye_exit_liquidity_multi(list_address: str) -> dict:
    """Exit liquidity for multiple Base-chain tokens from Birdeye v3 (max 50).
    NOTE: This endpoint only supports the Base (Ethereum L2) network.
    """
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/exit-liquidity/multiple",
        params={"list_address": list_address},
        headers=_birdeye_headers(chain="base"),
    )


async def birdeye_pair_overview(
    address: str,
    ui_amount_mode: str = None,
    chain: str = "solana",
) -> dict:
    """Comprehensive DEX pair overview from Birdeye v3 — token pair info, price, TVL, volume, fee tier.
    ui_amount_mode: raw or scaled (default: scaled). Supports 16+ chains.
    """
    params: dict = {"address": address}
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/pair/overview/single",
        params=params,
        headers=_birdeye_headers(chain=chain),
    )


async def birdeye_pair_overview_multi(
    list_address: str,
    ui_amount_mode: str = None,
    chain: str = "solana",
) -> dict:
    """Comprehensive DEX pair overview for multiple pairs from Birdeye v3 (max 20).
    ui_amount_mode: raw or scaled (default: scaled). Supports 16+ chains.
    """
    params: dict = {"list_address": list_address}
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/pair/overview/multiple",
        params=params,
        headers=_birdeye_headers(chain=chain),
    )


async def birdeye_price_stats(
    address: str,
    list_timeframe: str = None,
    ui_amount_mode: str = None,
) -> dict:
    """Price statistics for a token across multiple timeframes from Birdeye v3.
    list_timeframe: comma-separated e.g. '1h,4h,24h,7d' (options: 1m 5m 30m 1h 2h 4h 8h 24h 2d 3d 7d)
    ui_amount_mode: raw, scaled, or both (default: raw)
    Returns: current price, high/low, price change % per timeframe.
    """
    params: dict = {"address": address}
    if list_timeframe is not None:
        params["list_timeframe"] = list_timeframe
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/price/stats/single",
        params=params,
        headers=_birdeye_headers(),
    )


async def birdeye_price_stats_multi(
    list_address: str,
    list_timeframe: str = None,
    ui_amount_mode: str = None,
) -> dict:
    """Price statistics for multiple tokens across multiple timeframes from Birdeye v3.
    list_address: comma-separated mint addresses
    list_timeframe: comma-separated e.g. '1h,24h,7d' (options: 1m 5m 30m 1h 2h 4h 8h 24h 2d 3d 7d)
    ui_amount_mode: raw, scaled, or both (default: raw)
    """
    return await _post(
        f"{BIRDEYE_BASE}/defi/v3/price/stats/multiple",
        body={"list_address": list_address},
        headers=_birdeye_headers(),
        params={"list_timeframe": list_timeframe, "ui_amount_mode": ui_amount_mode},
    )


async def birdeye_token_list(
    sort_by: str = "market_cap",
    sort_type: str = "desc",
    min_liquidity: float = None,
    max_liquidity: float = None,
    min_market_cap: float = None,
    max_market_cap: float = None,
    min_fdv: float = None,
    max_fdv: float = None,
    min_holder: int = None,
    offset: int = None,
    limit: int = 50,
    ui_amount_mode: str = None,
) -> dict:
    """Filtered & sorted token list from Birdeye v3.
    sort_by: liquidity | market_cap | fdv | recent_listing_time | last_trade_unix_time | holder
             | volume_1h_usd | volume_24h_usd | volume_7d_usd | price_change_1h_percent
             | price_change_24h_percent | price_change_7d_percent | trade_1h_count | trade_24h_count
    sort_type: desc | asc
    limit: 1-100 (default 50)
    """
    params: dict = {"sort_by": sort_by, "sort_type": sort_type, "limit": limit}
    if min_liquidity is not None:
        params["min_liquidity"] = min_liquidity
    if max_liquidity is not None:
        params["max_liquidity"] = max_liquidity
    if min_market_cap is not None:
        params["min_market_cap"] = min_market_cap
    if max_market_cap is not None:
        params["max_market_cap"] = max_market_cap
    if min_fdv is not None:
        params["min_fdv"] = min_fdv
    if max_fdv is not None:
        params["max_fdv"] = max_fdv
    if min_holder is not None:
        params["min_holder"] = min_holder
    if offset is not None:
        params["offset"] = offset
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/list",
        params=params,
        headers=_birdeye_headers(),
    )


async def birdeye_token_list_scroll(
    sort_by: str = "market_cap",
    sort_type: str = "desc",
    scroll_id: str = None,
    limit: int = 100,
    min_liquidity: float = None,
    max_liquidity: float = None,
    min_market_cap: float = None,
    max_market_cap: float = None,
    min_fdv: float = None,
    max_fdv: float = None,
    min_holder: int = None,
    min_recent_listing_time: int = None,
    max_recent_listing_time: int = None,
    min_last_trade_unix_time: int = None,
    max_last_trade_unix_time: int = None,
    min_volume_24h_usd: float = None,
    min_price_change_24h_percent: float = None,
    min_trade_24h_count: int = None,
    ui_amount_mode: str = None,
) -> dict:
    """Large paginated token list from Birdeye v3 (max 5000 per page, scroll-based pagination).
    sort_by: same options as birdeye_token_list
    scroll_id: pass the scroll_id from previous response to get next page
    NOTE: Rate-limited to 1 scroll_id per 30 seconds per account.
    """
    params: dict = {"sort_by": sort_by, "sort_type": sort_type, "limit": limit}
    if scroll_id is not None:
        params["scroll_id"] = scroll_id
    if min_liquidity is not None:
        params["min_liquidity"] = min_liquidity
    if max_liquidity is not None:
        params["max_liquidity"] = max_liquidity
    if min_market_cap is not None:
        params["min_market_cap"] = min_market_cap
    if max_market_cap is not None:
        params["max_market_cap"] = max_market_cap
    if min_fdv is not None:
        params["min_fdv"] = min_fdv
    if max_fdv is not None:
        params["max_fdv"] = max_fdv
    if min_holder is not None:
        params["min_holder"] = min_holder
    if min_recent_listing_time is not None:
        params["min_recent_listing_time"] = min_recent_listing_time
    if max_recent_listing_time is not None:
        params["max_recent_listing_time"] = max_recent_listing_time
    if min_last_trade_unix_time is not None:
        params["min_last_trade_unix_time"] = min_last_trade_unix_time
    if max_last_trade_unix_time is not None:
        params["max_last_trade_unix_time"] = max_last_trade_unix_time
    if min_volume_24h_usd is not None:
        params["min_volume_24h_usd"] = min_volume_24h_usd
    if min_price_change_24h_percent is not None:
        params["min_price_change_24h_percent"] = min_price_change_24h_percent
    if min_trade_24h_count is not None:
        params["min_trade_24h_count"] = min_trade_24h_count
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/list/scroll",
        params=params,
        headers=_birdeye_headers(),
    )


async def birdeye_tokenlist_v1(
    sort_by: str = "v24hUSD",
    sort_type: str = "desc",
    offset: int = None,
    limit: int = 50,
    min_liquidity: float = 100,
    max_liquidity: float = None,
    ui_amount_mode: str = None,
) -> dict:
    """Simple token list from Birdeye v1 — sorted by volume/market cap/liquidity/price change.
    sort_by: v24hUSD | mc | v24hChangePercent | liquidity
    sort_type: desc | asc
    min_liquidity: minimum liquidity in USD (default: 100)
    limit: 1-50 (default 50)
    """
    params: dict = {"sort_by": sort_by, "sort_type": sort_type, "limit": limit, "min_liquidity": min_liquidity}
    if offset is not None:
        params["offset"] = offset
    if max_liquidity is not None:
        params["max_liquidity"] = max_liquidity
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/tokenlist",
        params=params,
        headers=_birdeye_headers(),
    )


async def birdeye_new_listings(
    limit: int = 20,
    time_to: int = None,
    meme_platform_enabled: bool = False,
) -> dict:
    """Newly listed tokens from Birdeye v2 — freshest listings on Solana.
    limit: 1-20 (default 20)
    time_to: Unix timestamp — fetch listings before this time (for pagination)
    meme_platform_enabled: include pump.fun and other meme launchpads (Solana only, default: false)
    """
    params: dict = {"limit": limit, "meme_platform_enabled": meme_platform_enabled}
    if time_to is not None:
        params["time_to"] = time_to
    return await _get(
        f"{BIRDEYE_BASE}/defi/v2/tokens/new_listing",
        params=params,
        headers=_birdeye_headers(),
    )


async def birdeye_token_markets(
    address: str,
    sort_by: str = "liquidity",
    sort_type: str = "desc",
    offset: int = None,
    limit: int = 10,
) -> dict:
    """All DEX markets/pools for a token from Birdeye v2 — sorted by liquidity or volume.
    sort_by: liquidity | volume24h
    sort_type: desc | asc
    limit: 1-20 (default 10)
    """
    params: dict = {"address": address, "sort_by": sort_by, "sort_type": sort_type, "limit": limit}
    if offset is not None:
        params["offset"] = offset
    return await _get(
        f"{BIRDEYE_BASE}/defi/v2/markets",
        params=params,
        headers=_birdeye_headers(),
    )


# ─── Birdeye Transaction Endpoints ───────────────────────────────────────────

async def birdeye_token_txs(
    address: str,
    tx_type: str = "swap",
    limit: int = 50,
    offset: int = None,
    sort_by: str = "block_unix_time",
    sort_type: str = "desc",
    source: str = None,
    owner: str = None,
    pool_id: str = None,
    before_time: int = None,
    after_time: int = None,
    before_block_number: int = None,
    after_block_number: int = None,
    ui_amount_mode: str = None,
) -> dict:
    """Token transactions from Birdeye v3 — richest transaction endpoint.
    tx_type: swap | buy | sell | add | remove | all
    source: raydium | jupiter | orca | meteora | pump_fun | ...
    owner: filter to a specific wallet address
    pool_id: filter to a specific liquidity pool
    before_time / after_time: Unix timestamp range (seconds)
    limit: 1-100
    """
    params: dict = {"address": address, "tx_type": tx_type, "limit": limit,
                    "sort_by": sort_by, "sort_type": sort_type}
    if offset is not None:
        params["offset"] = offset
    if source is not None:
        params["source"] = source
    if owner is not None:
        params["owner"] = owner
    if pool_id is not None:
        params["pool_id"] = pool_id
    if before_time is not None:
        params["before_time"] = before_time
    if after_time is not None:
        params["after_time"] = after_time
    if before_block_number is not None:
        params["before_block_number"] = before_block_number
    if after_block_number is not None:
        params["after_block_number"] = after_block_number
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/txs",
        params=params,
        headers=_birdeye_headers(),
    )


async def birdeye_txs_all(
    tx_type: str = "swap",
    limit: int = 50,
    offset: int = None,
    sort_by: str = "block_unix_time",
    sort_type: str = "desc",
    source: str = None,
    owner: str = None,
    pool_id: str = None,
    before_time: int = None,
    after_time: int = None,
    before_block_number: int = None,
    after_block_number: int = None,
    ui_amount_mode: str = None,
) -> dict:
    """All Solana DeFi transactions from Birdeye v3 — not filtered to a specific token.
    tx_type: swap | add | remove | all
    source: raydium | jupiter | orca | meteora | ...
    owner / pool_id: optional filters
    limit: 1-100
    """
    params: dict = {"tx_type": tx_type, "limit": limit,
                    "sort_by": sort_by, "sort_type": sort_type}
    if offset is not None:
        params["offset"] = offset
    if source is not None:
        params["source"] = source
    if owner is not None:
        params["owner"] = owner
    if pool_id is not None:
        params["pool_id"] = pool_id
    if before_time is not None:
        params["before_time"] = before_time
    if after_time is not None:
        params["after_time"] = after_time
    if before_block_number is not None:
        params["before_block_number"] = before_block_number
    if after_block_number is not None:
        params["after_block_number"] = after_block_number
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/txs",
        params=params,
        headers=_birdeye_headers(),
    )


async def birdeye_txs_recent(
    tx_type: str = "swap",
    limit: int = 50,
    offset: int = None,
    owner: str = None,
    before_time: int = None,
    after_time: int = None,
    before_block_number: int = None,
    after_block_number: int = None,
    ui_amount_mode: str = None,
) -> dict:
    """Most recent Solana DeFi transactions from Birdeye v3 (up to 500 per page).
    tx_type: swap | add | remove | all
    owner: filter to a specific wallet
    limit: 1-500
    """
    params: dict = {"tx_type": tx_type, "limit": limit}
    if offset is not None:
        params["offset"] = offset
    if owner is not None:
        params["owner"] = owner
    if before_time is not None:
        params["before_time"] = before_time
    if after_time is not None:
        params["after_time"] = after_time
    if before_block_number is not None:
        params["before_block_number"] = before_block_number
    if after_block_number is not None:
        params["after_block_number"] = after_block_number
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/txs/recent",
        params=params,
        headers=_birdeye_headers(),
    )


async def birdeye_token_txs_v1(
    address: str,
    sort_type: str = "desc",
    tx_type: str = "swap",
    limit: int = 50,
    offset: int = None,
    ui_amount_mode: str = None,
) -> dict:
    """Token transactions from Birdeye v1 — simpler, faster endpoint.
    tx_type: swap | add | remove | all
    sort_type: desc (newest first) | asc
    limit: 1-50
    """
    params: dict = {"address": address, "sort_type": sort_type, "tx_type": tx_type, "limit": limit}
    if offset is not None:
        params["offset"] = offset
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/txs/token",
        params=params,
        headers=_birdeye_headers(),
    )


async def birdeye_pair_txs(
    address: str,
    sort_type: str = "desc",
    tx_type: str = "swap",
    limit: int = 50,
    offset: int = None,
    ui_amount_mode: str = None,
) -> dict:
    """Transactions for a specific DEX pair/pool from Birdeye.
    address: pair/pool contract address
    tx_type: swap | add | remove | all
    sort_type: desc | asc
    limit: 1-50
    """
    params: dict = {"address": address, "sort_type": sort_type, "tx_type": tx_type, "limit": limit}
    if offset is not None:
        params["offset"] = offset
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/txs/pair",
        params=params,
        headers=_birdeye_headers(),
    )


async def birdeye_token_txs_by_time(
    address: str,
    tx_type: str = "swap",
    limit: int = 50,
    offset: int = None,
    before_time: int = None,
    after_time: int = None,
    ui_amount_mode: str = None,
) -> dict:
    """Token transactions filtered by Unix time range from Birdeye.
    tx_type: swap | add | remove | all
    before_time / after_time: Unix timestamp bounds (seconds)
    limit: 1-100
    """
    params: dict = {"address": address, "tx_type": tx_type, "limit": limit}
    if offset is not None:
        params["offset"] = offset
    if before_time is not None:
        params["before_time"] = before_time
    if after_time is not None:
        params["after_time"] = after_time
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/txs/token/seek_by_time",
        params=params,
        headers=_birdeye_headers(),
    )


async def birdeye_pair_txs_by_time(
    address: str,
    tx_type: str = "swap",
    limit: int = 50,
    offset: int = None,
    before_time: int = None,
    after_time: int = None,
    ui_amount_mode: str = None,
) -> dict:
    """Pair/pool transactions filtered by Unix time range from Birdeye.
    address: pair/pool contract address
    tx_type: swap | add | remove | all
    before_time / after_time: Unix timestamp bounds (seconds)
    limit: 1-50
    """
    params: dict = {"address": address, "tx_type": tx_type, "limit": limit}
    if offset is not None:
        params["offset"] = offset
    if before_time is not None:
        params["before_time"] = before_time
    if after_time is not None:
        params["after_time"] = after_time
    if ui_amount_mode is not None:
        params["ui_amount_mode"] = ui_amount_mode
    return await _get(
        f"{BIRDEYE_BASE}/defi/txs/pair/seek_by_time",
        params=params,
        headers=_birdeye_headers(),
    )


async def birdeye_trader_txs(
    address: str,
    tx_type: str = "swap",
    limit: int = 50,
    offset: int = None,
    before_time: int = None,
    after_time: int = None,
    ui_amount_mode: str = None,
) -> dict:
    """Transaction history for a specific trader wallet from Birdeye, filtered by time.
    address: wallet/trader address
    tx_type: swap | add | remove | all
    before_time / after_time: Unix timestamp bounds (seconds)
    limit: 1-100
    """
    return await _get(
        f"{BIRDEYE_BASE}/trader/txs/seek_by_time",
        params={
            "address": address, "tx_type": tx_type, "limit": limit, "offset": offset,
            "before_time": before_time, "after_time": after_time,
            "ui_amount_mode": ui_amount_mode,
        },
        headers=_birdeye_headers(),
    )


async def birdeye_token_txs_by_volume(
    token_address: str,
    volume_type: str = "usd",
    sort_type: str = "desc",
    tx_type: str = "swap",
    min_volume: float = None,
    max_volume: float = None,
    sort_by: str = "block_unix_time",
    source: str = None,
    owner: str = None,
    before_time: int = None,
    after_time: int = None,
    limit: int = 50,
    offset: int = None,
    ui_amount_mode: str = None,
) -> dict:
    """Token transactions filtered by trade volume from Birdeye v3 — find whale/large trades.
    volume_type: usd (filter by USD value) | amount (filter by token amount)
    min_volume / max_volume: volume filter bounds
    tx_type: swap | buy | sell | add | remove | all
    source: filter by DEX/AMM (raydium, jupiter, orca, ...)
    owner: filter to a specific wallet
    sort_type: desc | asc
    limit: 1-500. Solana only.
    """
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/txs-by-volume",
        params={
            "token_address": token_address, "volume_type": volume_type,
            "sort_type": sort_type, "tx_type": tx_type,
            "min_volume": min_volume, "max_volume": max_volume,
            "sort_by": sort_by, "source": source, "owner": owner,
            "before_time": before_time, "after_time": after_time,
            "limit": limit, "offset": offset, "ui_amount_mode": ui_amount_mode,
        },
        headers=_birdeye_headers(),
    )


async def birdeye_mint_burn_txs(
    address: str,
    type: str = "all",
    sort_type: str = "desc",
    sort_by: str = "block_time",
    before_time: int = None,
    after_time: int = None,
    offset: int = None,
    limit: int = 50,
) -> dict:
    """Token mint and burn transactions from Birdeye v3 — Solana only.
    type: all | mint | burn
    sort_type: desc | asc
    before_time / after_time: Unix timestamp bounds (seconds)
    limit: 1-100
    """
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/mint-burn-txs",
        params={
            "address": address, "type": type, "sort_type": sort_type, "sort_by": sort_by,
            "before_time": before_time, "after_time": after_time,
            "offset": offset, "limit": limit,
        },
        headers=_birdeye_headers(),
    )


# ─── Birdeye Misc handlers ────────────────────────────────────────────────────

async def birdeye_networks() -> dict:
    """List all blockchain networks supported by Birdeye."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/networks",
        params={},
        headers=_birdeye_headers(),
    )


async def birdeye_latest_block(chain: str = "solana") -> dict:
    """Latest block number on a chain from Birdeye.
    chain: solana | ethereum | arbitrum | bsc | base | polygon | optimism | avalanche | sui | aptos
    """
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/txs/latest-block",
        params={},
        headers=_birdeye_headers(chain),
    )


async def birdeye_token_creation_info(address: str, chain: str = "solana") -> dict:
    """Token creation transaction details — deployer wallet, creation time, initial supply."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/token_creation_info",
        params={"address": address},
        headers=_birdeye_headers(chain),
    )


async def birdeye_token_trending(
    sort_by: str = "rank",
    interval: str = "24h",
    sort_type: str = "asc",
    offset: int = None,
    limit: int = 20,
    ui_amount_mode: str = "scaled",
    chain: str = "solana",
) -> dict:
    """Trending tokens from Birdeye — sorted by rank, volumeUSD, or liquidity.
    interval: 1h | 4h | 24h. sort_by: rank | volumeUSD | liquidity.
    """
    return await _get(
        f"{BIRDEYE_BASE}/defi/token_trending",
        params={
            "sort_by": sort_by, "interval": interval, "sort_type": sort_type,
            "offset": offset, "limit": limit, "ui_amount_mode": ui_amount_mode,
        },
        headers=_birdeye_headers(chain),
    )


async def birdeye_meme_token_detail(address: str, chain: str = "solana") -> dict:
    """Detailed meme token data from Birdeye v3 — bonding curve progress, graduation status, platform.
    chain: solana | bsc | monad
    """
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/meme/detail/single",
        params={"address": address},
        headers=_birdeye_headers(chain),
    )


async def birdeye_meme_token_list(
    sort_by: str = "creation_time",
    sort_type: str = "desc",
    source: str = None,
    creator: str = None,
    graduated: bool = None,
    min_progress_percent: float = None,
    max_progress_percent: float = None,
    min_liquidity: float = None,
    max_liquidity: float = None,
    min_market_cap: float = None,
    max_market_cap: float = None,
    min_fdv: float = None,
    max_fdv: float = None,
    offset: int = None,
    limit: int = 100,
    chain: str = "solana",
) -> dict:
    """Meme token list from Birdeye v3 — filter by platform, graduation, progress, liquidity.
    sort_by: progress_percent | graduated_time | creation_time | liquidity | market_cap | fdv | recent_listing_time | holder | volume_* | price_change_* | trade_*_count
    source: all | pump_dot_fun | moonshot | raydium_launchlab | meteora_dynamic_bonding_curve | others
    """
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/meme/list",
        params={
            "sort_by": sort_by, "sort_type": sort_type, "source": source,
            "creator": creator, "graduated": graduated,
            "min_progress_percent": min_progress_percent, "max_progress_percent": max_progress_percent,
            "min_liquidity": min_liquidity, "max_liquidity": max_liquidity,
            "min_market_cap": min_market_cap, "max_market_cap": max_market_cap,
            "min_fdv": min_fdv, "max_fdv": max_fdv,
            "offset": offset, "limit": limit,
        },
        headers=_birdeye_headers(chain),
    )


async def birdeye_token_security(address: str, chain: str = "solana") -> dict:
    """Token security audit from Birdeye — mint authority, freeze authority, top holder concentration, LP status.
    chain: solana | ethereum | bsc | base | arbitrum | etc.
    """
    return await _get(
        f"{BIRDEYE_BASE}/defi/token_security",
        params={"address": address},
        headers=_birdeye_headers(chain),
    )


async def birdeye_smart_money_tokens(
    interval: str = "1d",
    trader_style: str = "all",
    sort_by: str = "smart_traders_no",
    sort_type: str = "desc",
    offset: int = None,
    limit: int = 20,
) -> dict:
    """Tokens being accumulated by smart money wallets — Solana only.
    interval: 1d | 7d | 30d.
    trader_style: all | risk_averse | risk_balancers | trenchers.
    sort_by: net_flow | smart_traders_no | market_cap.
    """
    return await _get(
        f"{BIRDEYE_BASE}/smart-money/v1/token/list",
        params={
            "interval": interval, "trader_style": trader_style,
            "sort_by": sort_by, "sort_type": sort_type,
            "offset": offset, "limit": limit,
        },
        headers=_birdeye_headers(),
    )


async def birdeye_all_time_trades_single(
    address: str,
    time_frame: str = "24h",
    ui_amount_mode: str = "scaled",
    chain: str = "solana",
) -> dict:
    """All-time cumulative trade statistics for a single token over a time frame.
    time_frame: 1m | 5m | 30m | 1h | 2h | 4h | 8h | 24h | 3d | 7d | 14d | 30d | 90d | 180d | 1y | alltime
    """
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/all-time/trades/single",
        params={"address": address, "time_frame": time_frame, "ui_amount_mode": ui_amount_mode},
        headers=_birdeye_headers(chain),
    )


async def birdeye_all_time_trades_multi(
    list_address: str,
    time_frame: str = "24h",
    ui_amount_mode: str = "scaled",
    chain: str = "solana",
) -> dict:
    """All-time cumulative trade statistics for multiple tokens at once.
    list_address: comma-separated token addresses.
    time_frame: 1m | 5m | 30m | 1h | 2h | 4h | 8h | 24h | 3d | 7d | 14d | 30d | 90d | 180d | 1y | alltime
    """
    return await _post(
        f"{BIRDEYE_BASE}/defi/v3/all-time/trades/multiple",
        body={},
        headers=_birdeye_headers(chain),
        params={"time_frame": time_frame, "list_address": list_address, "ui_amount_mode": ui_amount_mode},
    )


async def birdeye_search(
    keyword: str = None,
    chain: str = "solana",
    target: str = "all",
    search_mode: str = "fuzzy",
    search_by: str = "symbol",
    sort_by: str = "liquidity",
    sort_type: str = "desc",
    verify_token: bool = None,
    markets: str = None,
    offset: int = None,
    limit: int = 20,
    ui_amount_mode: str = "scaled",
) -> dict:
    """Search tokens and markets on Birdeye by name, symbol, or address.
    target: all | token | market. search_mode: exact | fuzzy. search_by: combination | address | name | symbol.
    sort_by: fdv | marketcap | liquidity | price | price_change_24h_percent | trade_24h | volume_24h_usd | ...
    markets: comma-separated — Raydium, Meteora, Pump.fun, Orca, etc.
    """
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/search",
        params={
            "keyword": keyword, "chain": chain, "target": target,
            "search_mode": search_mode, "search_by": search_by,
            "sort_by": sort_by, "sort_type": sort_type,
            "verify_token": verify_token, "markets": markets,
            "offset": offset, "limit": limit, "ui_amount_mode": ui_amount_mode,
        },
        headers=_birdeye_headers(chain),
    )


async def birdeye_credits(time_from: int = None, time_to: int = None) -> dict:
    """API credit usage for the current Birdeye API key — check remaining quota."""
    return await _get(
        f"{BIRDEYE_BASE}/utils/v1/credits",
        params={"time_from": time_from, "time_to": time_to},
        headers=_birdeye_headers(),
    )


# ─── Birdeye Balance / Transfer handlers ─────────────────────────────────────

async def birdeye_wallet_balance_change(
    address: str,
    token_address: str = None,
    time_from: int = None,
    time_to: int = None,
    type: str = None,
    change_type: str = None,
    offset: int = None,
    limit: int = 20,
    ui_amount_mode: str = "scaled",
    chain: str = "solana",
) -> dict:
    """Wallet balance change history — inflows and outflows per token over time.
    type: SOL | SPL. change_type: increase | decrease.
    """
    return await _get(
        f"{BIRDEYE_BASE}/wallet/v2/balance-change",
        params={
            "address": address, "token_address": token_address,
            "time_from": time_from, "time_to": time_to,
            "type": type, "change_type": change_type,
            "offset": offset, "limit": limit, "ui_amount_mode": ui_amount_mode,
        },
        headers=_birdeye_headers(chain),
    )


async def birdeye_wallet_token_balance(
    wallet: str,
    token_addresses: list,
    chain: str = "solana",
) -> dict:
    """Current balance of specific tokens in a wallet — batch lookup."""
    return await _post(
        f"{BIRDEYE_BASE}/wallet/v2/token-balance",
        body={"wallet": wallet, "token_addresses": token_addresses},
        headers=_birdeye_headers(chain),
    )


async def birdeye_token_transfers(
    token_address: str,
    time_from: float = None,
    time_to: float = None,
    from_amount: float = None,
    to_amount: float = None,
    from_value: float = None,
    to_value: float = None,
    from_wallet: str = None,
    to_wallet: str = None,
    cursor: str = None,
    limit: int = None,
) -> dict:
    """Token transfer list — all on-chain transfers of a token with optional filters.
    Filter by time range, amount range, value range, sender/receiver wallet.
    cursor: pagination cursor from previous response.
    """
    body = {"token_address": token_address}
    for k, v in [
        ("time_from", time_from), ("time_to", time_to),
        ("from_amount", from_amount), ("to_amount", to_amount),
        ("from_value", from_value), ("to_value", to_value),
        ("from_wallet", from_wallet), ("to_wallet", to_wallet),
        ("cursor", cursor), ("limit", limit),
    ]:
        if v is not None:
            body[k] = v
    return await _post(
        f"{BIRDEYE_BASE}/token/v1/transfer",
        body=body,
        headers=_birdeye_headers(),
    )


async def birdeye_token_transfer_total(
    token_address: str,
    time_from: float = None,
    time_to: float = None,
    from_amount: float = None,
    to_amount: float = None,
    from_value: float = None,
    to_value: float = None,
    from_wallet: str = None,
    to_wallet: str = None,
) -> dict:
    """Total count of token transfers matching filters — aggregate without listing."""
    body = {"token_address": token_address}
    for k, v in [
        ("time_from", time_from), ("time_to", time_to),
        ("from_amount", from_amount), ("to_amount", to_amount),
        ("from_value", from_value), ("to_value", to_value),
        ("from_wallet", from_wallet), ("to_wallet", to_wallet),
    ]:
        if v is not None:
            body[k] = v
    return await _post(
        f"{BIRDEYE_BASE}/token/v1/transfer/total",
        body=body,
        headers=_birdeye_headers(),
    )


async def birdeye_wallet_transfers(
    wallet: str,
    token_address: str = None,
    flow: str = None,
    time_from: float = None,
    time_to: float = None,
    from_amount: float = None,
    to_amount: float = None,
    from_value: float = None,
    to_value: float = None,
    from_wallet: str = None,
    to_wallet: str = None,
    cursor: str = None,
    limit: int = None,
    chain: str = "solana",
) -> dict:
    """Transfer history for a wallet — all token movements in/out.
    flow: in | out (optional direction filter).
    cursor: pagination cursor from previous response.
    """
    body = {"wallet": wallet}
    for k, v in [
        ("token_address", token_address), ("flow", flow),
        ("time_from", time_from), ("time_to", time_to),
        ("from_amount", from_amount), ("to_amount", to_amount),
        ("from_value", from_value), ("to_value", to_value),
        ("from_wallet", from_wallet), ("to_wallet", to_wallet),
        ("cursor", cursor), ("limit", limit),
    ]:
        if v is not None:
            body[k] = v
    return await _post(
        f"{BIRDEYE_BASE}/wallet/v2/transfer",
        body=body,
        headers=_birdeye_headers(chain),
    )


async def birdeye_wallet_transfer_total(
    wallet: str,
    token_address: str = None,
    flow: str = None,
    time_from: float = None,
    time_to: float = None,
    from_amount: float = None,
    to_amount: float = None,
    from_value: float = None,
    to_value: float = None,
    from_wallet: str = None,
    to_wallet: str = None,
    chain: str = "solana",
) -> dict:
    """Total count of wallet transfers matching filters — aggregate without listing."""
    body = {"wallet": wallet}
    for k, v in [
        ("token_address", token_address), ("flow", flow),
        ("time_from", time_from), ("time_to", time_to),
        ("from_amount", from_amount), ("to_amount", to_amount),
        ("from_value", from_value), ("to_value", to_value),
        ("from_wallet", from_wallet), ("to_wallet", to_wallet),
    ]:
        if v is not None:
            body[k] = v
    return await _post(
        f"{BIRDEYE_BASE}/wallet/v2/transfer/total",
        body=body,
        headers=_birdeye_headers(chain),
    )


async def birdeye_wallet_single_token_balance(
    wallet: str,
    token_address: str,
    ui_amount_mode: str = "scaled",
    chain: str = "solana",
) -> dict:
    """Current balance of one specific token in a wallet (v1 beta)."""
    return await _get(
        f"{BIRDEYE_BASE}/v1/wallet/token_balance",
        params={"wallet": wallet, "token_address": token_address, "ui_amount_mode": ui_amount_mode},
        headers=_birdeye_headers(chain),
    )


# ─── Birdeye Holder handlers ─────────────────────────────────────────────────

async def birdeye_token_holders(
    address: str,
    offset: int = None,
    limit: int = 100,
    ui_amount_mode: str = "scaled",
) -> dict:
    """Top token holders list from Birdeye v3 — address, balance, % of supply."""
    return await _get(
        f"{BIRDEYE_BASE}/defi/v3/token/holder",
        params={"address": address, "offset": offset, "limit": limit, "ui_amount_mode": ui_amount_mode},
        headers=_birdeye_headers(),
    )


async def birdeye_holder_batch(
    wallets: list,
    token_address: str,
    ui_amount_mode: str = "scaled",
) -> dict:
    """Check token holdings for a batch of wallets — how much of token X each wallet holds."""
    return await _post(
        f"{BIRDEYE_BASE}/token/v1/holder/batch",
        body={"wallets": wallets, "token_address": token_address},
        headers=_birdeye_headers(),
        params={"ui_amount_mode": ui_amount_mode},
    )


async def birdeye_holder_distribution(
    token_address: str,
    address_type: str = "wallet",
    mode: str = "top",
    top_n: int = 10,
    min_percent: float = None,
    max_percent: float = None,
    include_list: bool = True,
    offset: int = None,
    limit: int = 50,
) -> dict:
    """Token holder distribution — top-N holders or percent-range holders.
    mode: top (top N holders) | percent (filter by supply %)
    address_type: wallet | token_account
    """
    return await _get(
        f"{BIRDEYE_BASE}/holder/v1/distribution",
        params={
            "token_address": token_address, "address_type": address_type,
            "mode": mode, "top_n": top_n,
            "min_percent": min_percent, "max_percent": max_percent,
            "include_list": include_list, "offset": offset, "limit": limit,
        },
        headers=_birdeye_headers(),
    )


async def birdeye_holder_profile(
    token_address: str,
    interval: str = "1h",
    ui_amount_mode: str = "scaled",
    include_zero_balance: bool = True,
) -> dict:
    """Token holder profile summary — total holders, new/churned holders, volume breakdown.
    interval: 1h (default)
    """
    return await _get(
        f"{BIRDEYE_BASE}/token/v1/holder-profile",
        params={
            "token_address": token_address, "interval": interval,
            "ui_amount_mode": ui_amount_mode, "include_zero_balance": include_zero_balance,
        },
        headers=_birdeye_headers(),
    )


async def birdeye_holder_positions(
    token_address: str,
    labels: str = None,
    sort_by: str = "amount",
    order_type: str = "desc",
    ui_amount_mode: str = "scaled",
    include_zero_balance: bool = True,
    offset: int = None,
    limit: int = 50,
) -> dict:
    """Token holder positions with labels — filter by bundler, sniper, insider, dev tags.
    labels: comma-separated — bundler | sniper | insider | dev (optional filter)
    sort_by: amount (default)
    """
    return await _get(
        f"{BIRDEYE_BASE}/token/v1/holder-positions",
        params={
            "token_address": token_address, "labels": labels,
            "sort_by": sort_by, "order_type": order_type,
            "ui_amount_mode": ui_amount_mode, "include_zero_balance": include_zero_balance,
            "offset": offset, "limit": limit,
        },
        headers=_birdeye_headers(),
    )


# ─── Birdeye Wallet / Trader handlers ────────────────────────────────────────

async def birdeye_wallet_current_net_worth(
    wallet: str,
    filter_value: float = None,
    sort_by: str = "value",
    sort_type: str = "desc",
    limit: int = 20,
    offset: int = None,
    flags: list = None,
    chain: str = "solana",
) -> dict:
    """Current portfolio snapshot — token balances + USD values for a wallet."""
    params = {
        "wallet": wallet, "filter_value": filter_value,
        "sort_by": sort_by, "sort_type": sort_type,
        "limit": limit, "offset": offset,
    }
    if flags:
        params["flags"] = flags
    return await _get(f"{BIRDEYE_BASE}/wallet/v2/current-net-worth", params=params, headers=_birdeye_headers(chain))


async def birdeye_wallet_net_worth_history(
    wallet: str,
    count: int = 7,
    direction: str = "back",
    time: str = None,
    type: str = "1d",
    sort_type: str = "desc",
    chain: str = "solana",
) -> dict:
    """Historical net worth chart for a wallet (daily or hourly)."""
    return await _get(
        f"{BIRDEYE_BASE}/wallet/v2/net-worth",
        params={
            "wallet": wallet, "count": count, "direction": direction,
            "time": time, "type": type, "sort_type": sort_type,
        },
        headers=_birdeye_headers(chain),
    )


async def birdeye_wallet_net_worth_multi(
    wallets: list,
    chain: str = "solana",
) -> dict:
    """Net worth summary for up to 100 wallets in a single request."""
    return await _post(
        f"{BIRDEYE_BASE}/wallet/v2/net-worth-summary/multiple",
        body={"wallets": wallets},
        headers=_birdeye_headers(chain),
    )


async def birdeye_wallet_net_worth_details(
    wallet: str,
    time: str = None,
    type: str = "1d",
    sort_type: str = "desc",
    limit: int = 20,
    offset: int = None,
    chain: str = "solana",
) -> dict:
    """Detailed asset breakdown of a wallet's net worth at a specific point in time."""
    return await _get(
        f"{BIRDEYE_BASE}/wallet/v2/net-worth-details",
        params={
            "wallet": wallet, "time": time, "type": type,
            "sort_type": sort_type, "limit": limit, "offset": offset,
        },
        headers=_birdeye_headers(chain),
    )


async def birdeye_wallet_pnl_summary(
    wallet: str,
    duration: str = "7d",
    chain: str = "solana",
) -> dict:
    """Overall PnL summary for a wallet — realized, unrealized, total return.
    duration: all | 90d | 30d | 7d | 24h
    chain: solana | base
    """
    return await _get(
        f"{BIRDEYE_BASE}/wallet/v2/pnl/summary",
        params={"wallet": wallet, "duration": duration},
        headers=_birdeye_headers(chain),
    )


async def birdeye_wallet_pnl_details(
    wallet: str,
    token_addresses: list = None,
    duration: str = "all",
    sort_type: str = "desc",
    sort_by: str = "last_trade",
    limit: int = 10,
    offset: int = None,
    chain: str = "solana",
) -> dict:
    """Per-token PnL breakdown for a wallet — realized + unrealized per holding.
    duration: all | 90d | 30d | 7d | 24h
    chain: solana | base
    """
    body = {
        "wallet": wallet, "duration": duration,
        "sort_type": sort_type, "sort_by": sort_by,
        "limit": limit, "offset": offset,
    }
    if token_addresses:
        body["token_addresses"] = token_addresses
    return await _post(
        f"{BIRDEYE_BASE}/wallet/v2/pnl/details",
        body={k: v for k, v in body.items() if v is not None},
        headers=_birdeye_headers(chain),
    )


async def birdeye_wallet_pnl_token(
    wallet: str,
    token_addresses: str,
    chain: str = "solana",
) -> dict:
    """PnL for specific tokens in a wallet (deprecated — prefer birdeye_wallet_pnl_details).
    token_addresses: comma-separated token mint addresses
    """
    return await _get(
        f"{BIRDEYE_BASE}/wallet/v2/pnl",
        params={"wallet": wallet, "token_addresses": token_addresses},
        headers=_birdeye_headers(chain),
    )


async def birdeye_wallet_pnl_multi(
    token_address: str,
    wallets: str,
    chain: str = "solana",
) -> dict:
    """PnL for multiple wallets on a single token — compare trader performance.
    wallets: comma-separated wallet addresses
    """
    return await _get(
        f"{BIRDEYE_BASE}/wallet/v2/pnl/multiple",
        params={"token_address": token_address, "wallets": wallets},
        headers=_birdeye_headers(chain),
    )


async def birdeye_wallet_first_funded(
    wallets: list,
    token_address: str = None,
    chain: str = "solana",
) -> dict:
    """First funding transaction for each wallet — useful for wallet age / origin analysis."""
    body = {"wallets": wallets}
    if token_address:
        body["token_address"] = token_address
    return await _post(
        f"{BIRDEYE_BASE}/wallet/v2/tx/first-funded",
        body=body,
        headers=_birdeye_headers(chain),
    )


async def birdeye_token_top_traders(
    address: str,
    time_frame: str = "24h",
    sort_type: str = "desc",
    sort_by: str = "volume",
    offset: int = None,
    limit: int = 10,
    ui_amount_mode: str = "scaled",
    chain: str = "solana",
) -> dict:
    """Top traders for a token — ranked by volume, trade count, total/realized/unrealized PnL.
    time_frame: 30m | 1h | 2h | 4h | 6h | 8h | 12h | 24h | 2d | 3d | 7d | 14d | 30d | 60d | 90d
    sort_by: volume | trade | total_pnl | unrealized_pnl | realized_pnl | volume_usd
    """
    return await _get(
        f"{BIRDEYE_BASE}/defi/v2/tokens/top_traders",
        params={
            "address": address, "time_frame": time_frame,
            "sort_type": sort_type, "sort_by": sort_by,
            "offset": offset, "limit": limit, "ui_amount_mode": ui_amount_mode,
        },
        headers=_birdeye_headers(chain),
    )


async def birdeye_trader_gainers_losers(
    type: str = "1W",
    sort_by: str = "PnL",
    sort_type: str = "desc",
    offset: int = None,
    limit: int = 10,
    chain: str = "solana",
) -> dict:
    """Global top-gaining or top-losing traders across Solana (or other chains).
    type: yesterday | today | 1W
    sort_by: PnL
    sort_type: desc (top gainers) | asc (top losers)
    """
    return await _get(
        f"{BIRDEYE_BASE}/trader/gainers-losers",
        params={
            "type": type, "sort_by": sort_by, "sort_type": sort_type,
            "offset": offset, "limit": limit,
        },
        headers=_birdeye_headers(chain),
    )


async def birdeye_wallet_supported_chains() -> dict:
    """List of all blockchain networks supported by Birdeye wallet APIs."""
    return await _get(
        f"{BIRDEYE_BASE}/v1/wallet/list_supported_chain",
        params={},
        headers=_birdeye_headers(),
    )


async def birdeye_wallet_tx_list(
    wallet: str,
    limit: int = 100,
    before: str = None,
    ui_amount_mode: str = "scaled",
    chain: str = "solana",
) -> dict:
    """Wallet transaction history from Birdeye v1 — swaps, transfers, LP actions.
    before: tx hash to paginate from (Solana only)
    limit: 1-100
    """
    return await _get(
        f"{BIRDEYE_BASE}/v1/wallet/tx_list",
        params={
            "wallet": wallet, "limit": limit,
            "before": before, "ui_amount_mode": ui_amount_mode,
        },
        headers=_birdeye_headers(chain),
    )


async def birdeye_wallet_token_list(
    wallet: str,
    ui_amount_mode: str = "scaled",
    chain: str = "solana",
) -> dict:
    """Wallet token holdings from Birdeye v1 (deprecated — prefer birdeye_wallet_current_net_worth).
    Returns all tokens held with balances and USD values.
    """
    return await _get(
        f"{BIRDEYE_BASE}/v1/wallet/token_list",
        params={"wallet": wallet, "ui_amount_mode": ui_amount_mode},
        headers=_birdeye_headers(chain),
    )


# ─── Tensor Trade handlers ───────────────────────────────────────────────────

def _tensor_headers() -> dict:
    return {"X-TENSOR-API-KEY": TENSOR_KEY, "Content-Type": "application/json"}


async def tensor_edit_bid(
    bid_state_address: str,
    blockhash: str,
    price: float = None,
    quantity: int = None,
    expire_in: int = None,
    maker_broker: str = None,
    private_taker: str = None,
    use_shared_escrow: bool = None,
    compute: int = None,
    priority_micro_lamports: int = None,
) -> dict:
    """Fetch serialized Tensor bid-edit transaction data — no auth required.
    Returns raw transaction bytes (not executed). Caller signs and submits.
    """
    return await _get(
        f"{TENSOR_BASE}/tx/edit_bid",
        params={
            "bidStateAddress": bid_state_address,
            "blockhash": blockhash,
            "price": price,
            "quantity": quantity,
            "expireIn": expire_in,
            "makerBroker": maker_broker,
            "privateTaker": private_taker,
            "useSharedEscrow": use_shared_escrow,
            "compute": compute,
            "priorityMicroLamports": priority_micro_lamports,
        },
        headers=_tensor_headers(),
    )


# ── Tensor data endpoints ────────────────────────────────────────────────────

async def tensor_mint_proof(mint: str) -> dict:
    """Merkle proof for a compressed NFT (cNFT) on Tensor — required for cNFT transactions."""
    return await _get(f"{TENSOR_BASE}/mint/proof", params={"mint": mint}, headers=_tensor_headers())


async def tensor_mint_collection(
    coll_id: str,
    sort_by: str,
    limit: int,
    cursor: str = None,
    filters: str = None,
) -> dict:
    """Browse all NFTs in a collection with sorting and optional filters."""
    return await _get(
        f"{TENSOR_BASE}/mint/collection",
        params={"collId": coll_id, "sortBy": sort_by, "limit": limit, "cursor": cursor, "filters": filters},
        headers=_tensor_headers(),
    )


async def tensor_mint_active_listings(
    coll_id: str,
    sort_by: str,
    limit: int,
    cursor: str = None,
    include_private_taker: bool = None,
    filters: str = None,
) -> dict:
    """Active listings for an NFT collection on Tensor."""
    return await _get(
        f"{TENSOR_BASE}/mint/active_listings",
        params={
            "collId": coll_id, "sortBy": sort_by, "limit": limit,
            "cursor": cursor, "includePrivateTaker": include_private_taker, "encodedFilters": filters,
        },
        headers=_tensor_headers(),
    )


async def tensor_mint_activity(
    mint: str,
    limit: int,
    tx_types: str = None,
    cursor: str = None,
) -> dict:
    """Transaction activity history for a specific NFT mint on Tensor."""
    return await _get(
        f"{TENSOR_BASE}/mint/activity",
        params={"mint": mint, "limit": limit, "txTypes": tx_types, "cursor": cursor},
        headers=_tensor_headers(),
    )


async def tensor_collections_browse(
    sort_by: str,
    limit: int,
    slug_displays: str = None,
    coll_ids: str = None,
    vocs: str = None,
    fvcs: str = None,
    page: int = None,
) -> dict:
    """Browse/list all NFT collections on Tensor with sorting and filtering."""
    return await _get(
        f"{TENSOR_BASE}/collections",
        params={
            "sortBy": sort_by, "limit": limit, "slugDisplays": slug_displays,
            "collIds": coll_ids, "vocs": vocs, "fvcs": fvcs, "page": page,
        },
        headers=_tensor_headers(),
    )


async def tensor_find_collection(filter: str) -> dict:
    """Find a collection by collId, slug, or slugDisplay."""
    return await _get(
        f"{TENSOR_BASE}/collections/find_collection",
        params={"filter": filter},
        headers=_tensor_headers(),
    )


async def tensor_search_collections(
    query: str = None,
    sort_by: str = None,
    limit: int = None,
    page: int = None,
) -> dict:
    """Search Tensor NFT collections by name/keyword."""
    return await _get(
        f"{TENSOR_BASE}/collections/search_collections",
        params={"query": query, "sortBy": sort_by, "limit": limit, "page": page},
        headers=_tensor_headers(),
    )


async def tensor_collections_mints(mints: str) -> dict:
    """Fetch collection metadata for a list of NFT mint addresses (comma-separated)."""
    return await _get(
        f"{TENSOR_BASE}/collections/mints",
        params={"mints": mints},
        headers=_tensor_headers(),
    )


async def tensor_collections_list(coll_id: str, limit: int, after: str = None) -> list:
    """Paginated list of all mint addresses in a collection."""
    return await _get(
        f"{TENSOR_BASE}/collections/list",
        params={"collId": coll_id, "limit": limit, "after": after},
        headers=_tensor_headers(),
    )


async def tensor_collections_traits(coll_id: str) -> dict:
    """All trait types and values for a collection, with rarity counts."""
    return await _get(
        f"{TENSOR_BASE}/collections/traits",
        params={"collId": coll_id},
        headers=_tensor_headers(),
    )


async def tensor_collections_coll_bids(coll_id: str, limit: int, cursor: str = None) -> dict:
    """Active collection-level bids (offers) for an NFT collection."""
    return await _get(
        f"{TENSOR_BASE}/collections/coll_bids",
        params={"collId": coll_id, "limit": limit, "cursor": cursor},
        headers=_tensor_headers(),
    )


async def tensor_collections_nft_bids(mints: str) -> dict:
    """Active NFT-level bids for specific mint addresses (comma-separated)."""
    return await _get(
        f"{TENSOR_BASE}/collections/nft_bids",
        params={"mints": mints},
        headers=_tensor_headers(),
    )


async def tensor_collections_trait_bids(coll_id: str, limit: int, cursor: str = None) -> dict:
    """Active trait-level bids for a collection."""
    return await _get(
        f"{TENSOR_BASE}/collections/trait_bids",
        params={"collId": coll_id, "limit": limit, "cursor": cursor},
        headers=_tensor_headers(),
    )


async def tensor_collections_pools(coll_id: str) -> dict:
    """TSWAP AMM pools for an NFT collection."""
    return await _get(
        f"{TENSOR_BASE}/collections/pools",
        params={"collId": coll_id},
        headers=_tensor_headers(),
    )


async def tensor_collections_tamm_pools(coll_id: str) -> dict:
    """TAMM AMM pools for an NFT collection."""
    return await _get(
        f"{TENSOR_BASE}/collections/tamm_pools",
        params={"collId": coll_id},
        headers=_tensor_headers(),
    )


async def tensor_collections_tx_history(
    limit: int,
    coll_id: str = None,
    wallet: str = None,
    tx_types: str = None,
    sources: str = None,
) -> dict:
    """Transaction history across collections on Tensor. limit is required; all others optional."""
    return await _get(
        f"{TENSOR_BASE}/collections/tx_history",
        params={"limit": limit, "collId": coll_id, "wallet": wallet, "txTypes": tx_types, "sources": sources},
        headers=_tensor_headers(),
    )


async def tensor_whitelist_info(coll_ids: str = None, whitelists: str = None) -> dict:
    """Fetch whitelist info for collections by collId or whitelist address."""
    return await _get(
        f"{TENSOR_BASE}/sdk/whitelist_info",
        params={"collIds": coll_ids, "whitelists": whitelists},
        headers=_tensor_headers(),
    )


async def tensor_trait_bid_attributes(bid_state_address: str) -> dict:
    """Fetch trait bid attributes for a given bid state address on Tensor."""
    return await _get(
        f"{TENSOR_BASE}/sdk/trait_bid_attributes",
        params={"bidStateAddress": bid_state_address},
        headers=_tensor_headers(),
    )


async def tensor_refresh_metadata(mint: str) -> dict:
    """Trigger a metadata refresh for a specific NFT. 1-hour cooldown per mint."""
    return await _post(
        f"{TENSOR_BASE}/trigger/refresh_metadata",
        body={},
        params={"mint": mint},
        headers=_tensor_headers(),
    )


async def tensor_refresh_rarities(coll_id: str) -> dict:
    """Trigger a rarity refresh for an entire collection. 1-hour cooldown per collection."""
    return await _post(
        f"{TENSOR_BASE}/trigger/refresh_rarities",
        body={},
        params={"collId": coll_id},
        headers=_tensor_headers(),
    )


# ─── Tool schemas for gpt-5.4-nano ────────────────────────────────────────────

TOOL_SCHEMAS = [
    # ── Jupiter ──────────────────────────────────────────────────────────────
    {
        "name": "jup_prices",
        "description": (
            "Get real-time USD prices for one or more tokens from Jupiter Price API v3. "
            "Returns per-token: usdPrice, priceChange24h (24h % change), liquidity (total USD liquidity), "
            "decimals, blockId (Solana block for price freshness), createdAt. "
            "Pass comma-separated mint addresses (max 50). "
            "Tokens with no recent trading activity may be absent from the response. "
            "Use for: 'SOL price', 'how much is BONK worth', 'compare JUP and WIF prices', 'batch price lookup'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mints": {"type": "string", "description": "Comma-separated mint addresses (max 50)"},
            },
            "required": ["mints"],
        },
    },
    {
        "name": "jup_search",
        "description": (
            "Search tokens by name, symbol, or comma-separated mint addresses (max 100) via Jupiter Tokens API v2. "
            "Returns ultra-rich data: id (mint), name, symbol, usdPrice, mcap, fdv, holderCount, liquidity, "
            "circSupply, totalSupply, twitter, website, dev (developer wallet), "
            "stats5m/1h/6h/24h (priceChange, buyVolume, sellVolume, buyOrganicVolume, sellOrganicVolume, "
            "numBuys, numSells, numTraders, numOrganicBuyers, numNetBuyers), "
            "audit (mintAuthorityDisabled, freezeAuthorityDisabled, topHoldersPercentage), "
            "organicScore (0-100), organicScoreLabel (high/medium/low), isVerified, tags, launchpad. "
            "Use for: 'find BONK token', 'search WIF', 'is this token verified', 'token audit info', "
            "'batch lookup by mint addresses'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Token name, symbol, or comma-separated mint addresses (max 100 mints)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "jup_recent",
        "description": (
            "Most recently launched tokens across all Solana launchpads (pump.fun, letsbonk.fun, Raydium LaunchLab). "
            "Always returns 30 tokens — tokens that recently had their first liquidity pool created. "
            "Includes live price, volume, holder count, and audit data for each new launch. "
            "Use for: 'new tokens launching', 'latest pump.fun launches', 'newest meme coins', 'fresh launchpad tokens'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "jup_trending",
        "description": (
            "Top trending/traded tokens on Solana by category and time interval from Jupiter. "
            "Filters out baseline tokens like SOL, USDC. Returns full MintInformation per token. "
            "category options: toptrending (momentum movers), toptraded (highest swap volume), "
            "toporganicscore (real human demand — filters bot/wash trading). "
            "interval options: 5m | 1h | 6h | 24h. limit: 1–100 (default 50). "
            "Returns: name, symbol, usdPrice, stats per interval, organicScore, audit, launchpad. "
            "Use for: 'what's trending on Jupiter', 'top traded tokens today', 'tokens with real organic demand', "
            "'5-minute movers', '24h trending list', 'Jupiter hot tokens'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "toptrending | toptraded | toporganicscore (default: toptrending)",
                },
                "interval": {"type": "string", "description": "5m | 1h | 6h | 24h (default: 1h)"},
                "limit": {"type": "integer", "description": "1–100, default 50"},
            },
        },
    },
    {
        "name": "jup_tokens_tag",
        "description": (
            "List all tokens belonging to a tag from Jupiter Tokens v2. "
            "query options (ONLY these two are supported): "
            "lst — liquid staking tokens (jitoSOL, mSOL, bSOL, stSOL, etc.), "
            "verified — audited & verified real projects (highest trust level on Jupiter). "
            "Returns full token data: price, mcap, audit, organic score, stats. "
            "Use for: 'show verified tokens on Jupiter', 'list LST/liquid staking tokens', "
            "'which staking tokens are on Jupiter', 'safe verified token list'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Tag filter — ONLY 'lst' or 'verified' are supported",
                },
                "limit": {"type": "integer", "description": "Max results to return (default: 50)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "jup_verify_eligibility",
        "description": (
            "Check if a token is eligible for Jupiter verification. "
            "Returns: tokenExists, isVerified, canVerify (can submit for verification), "
            "canMetadata (can update metadata), verificationError, metadataError. "
            "Requires JUPITER_API_KEY. "
            "Use for: 'can I verify this token on Jupiter', 'is this token verified already', "
            "'check Jupiter verification eligibility for mint X'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "token_id": {"type": "string", "description": "Token mint address to check"},
            },
            "required": ["token_id"],
        },
    },
    {
        "name": "jup_quote",
        "description": (
            "Jupiter swap quote — finds the best route across all Solana DEXes (Raydium, Orca, Meteora, etc.). "
            "Use this as the DEFAULT for any generic swap/exchange question when user doesn't specify a DEX. "
            "Returns outAmount, priceImpactPct, routePlan. "
            "Use for: 'how much SOL for 1000 USDC', 'swap X to Y', 'best price for BONK→SOL', "
            "'exchange rate between two tokens'. "
            "AMOUNTS in BASE UNITS: SOL ×1e9, USDC/USDT ×1e6, most SPL ×1e6, BONK ×1e5."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "input_mint":  {"type": "string", "description": "Input token mint address"},
                "output_mint": {"type": "string", "description": "Output token mint address"},
                "amount":      {"type": "integer", "description": "Amount in base units"},
                "swap_mode":   {"type": "string", "description": "'ExactIn' (default) or 'ExactOut'"},
                "slippage_bps":{"type": "integer", "description": "Slippage tolerance in bps (default 50 = 0.5%)"},
            },
            "required": ["input_mint", "output_mint", "amount"],
        },
    },
    # ── DexScreener ──────────────────────────────────────────────────────────
    {
        "name": "dex_trending",
        "description": (
            "Trending/boosted tokens on Solana from DexScreener — tokens with high momentum or paid boosts. "
            "Use for: 'what's trending', 'hot tokens right now', 'DexScreener trending'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "dex_search",
        "description": (
            "Search Solana DEX pairs by token name, symbol, or mint address on DexScreener. "
            "Results are pre-filtered to Solana chain only. "
            "Returns pairs with price, volume, liquidity per DEX. "
            "If pairs list is empty, try dex_token with the mint address instead. "
            "Use for: 'find BONK pairs', 'which DEX has SOL/USDC', 'search [token] on DEXes'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Token name, symbol, or mint address"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "dex_token",
        "description": (
            "Get all DEX trading pairs for a token mint address from DexScreener. "
            "Returns price, 24h volume, liquidity, buy/sell counts per DEX pair. "
            "Use for: 'where is BONK trading', 'best liquidity for X', 'BONK price and volume across DEXes'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mint": {"type": "string", "description": "Token mint address (base58)"},
            },
            "required": ["mint"],
        },
    },
    {
        "name": "dex_latest_pairs",
        "description": (
            "Newest token pairs/profiles created on Solana DEXes from DexScreener. "
            "Use for: 'new tokens launching', 'latest DEX listings', 'fresh pairs'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    # ── Meteora ──────────────────────────────────────────────────────────────
    {
        "name": "meteora_pools",
        "description": (
            "Meteora DLMM pools — token pair, TVL, 24h volume, price, and fee tier. "
            "Data sourced from DexScreener (Meteora's own API is offline). "
            "Pass a token symbol or name to search (default: SOL). "
            "Use for: 'Meteora pools', 'Meteora liquidity', 'best Meteora DLMM', 'SOL pools on Meteora'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "Token symbol or name to search (default: SOL)"},
                "limit": {"type": "integer", "description": "Number of pools (default: 15)"},
            },
        },
    },
    # ── Raydium ──────────────────────────────────────────────────────────────
    {
        "name": "raydium_prices",
        "description": (
            "Token prices from Raydium AMM — returns mint → USD price mapping for all Raydium-listed tokens. "
            "Complements Jupiter prices. Use for: 'Raydium price for X', 'token prices on Raydium'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "raydium_clmm_pools",
        "description": (
            "Legacy Raydium CLMM pool list (v2 API). Prefer raydium_pools(poolType=Concentrated) for richer data. "
            "Use only if raydium_pools fails or user explicitly requests legacy CLMM data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of pools to return (default: 20)"},
            },
        },
    },
    {
        "name": "raydium_swap_quote",
        "description": (
            "Raydium Trade API swap quote — exact price, route plan, price impact, slippage threshold. "
            "Use for: 'swap X SOL for USDC on Raydium', 'Raydium quote', 'how much USDC do I get for 1 SOL on Raydium', "
            "'Raydium ExactOut quote', 'Raydium swap price impact'. "
            "swapMode: ExactIn (specify input) or ExactOut (specify desired output). "
            "amount in base units (lamports for SOL=1e9, micro-USDC=1e6, etc.)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "inputMint": {"type": "string", "description": "Input token mint address"},
                "outputMint": {"type": "string", "description": "Output token mint address"},
                "amount": {"type": "integer", "description": "Amount in base units (lamports/micro-units)"},
                "slippageBps": {"type": "integer", "description": "Slippage in basis points (50=0.5%, 100=1%). Default: 50"},
                "swapMode": {"type": "string", "description": "ExactIn (input fixed) or ExactOut (output fixed). Default: ExactIn"},
                "txVersion": {"type": "string", "description": "V0 or LEGACY. Default: V0"},
            },
            "required": ["inputMint", "outputMint", "amount"],
        },
    },
    {
        "name": "raydium_pools",
        "description": (
            "Raydium pool list via API v3 — AMM, CLMM, and CPMM pools with TVL, 24h volume, fee rate, price. "
            "Use for: 'top Raydium pools', 'best Raydium LP', 'Raydium AMM pools', 'Raydium pools by TVL', "
            "'Raydium pools sorted by volume', 'Raydium CPMM pools', 'all Raydium pool types'. "
            "poolType: all | Standard | Concentrated | AllFarm | StandardFarm | ConcentratedFarm."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "poolType": {"type": "string", "description": "all | Standard | Concentrated | AllFarm | StandardFarm | ConcentratedFarm (default: all)"},
                "sortField": {"type": "string", "description": "volume24h | tvl | fee24h | apr24h | liquidity (default: volume24h)"},
                "sortType": {"type": "string", "description": "asc | desc (default: desc)"},
                "limit": {"type": "integer", "description": "Number of pools (default: 20, max: 100)"},
            },
        },
    },
    {
        "name": "raydium_pool_search",
        "description": (
            "Look up a specific Raydium pool by pool ID — returns full details: TVL, price, volume, fee rate, token pair. "
            "Use for: 'info on Raydium pool 3nMFw...', 'details for this Raydium pool address', "
            "'what tokens are in Raydium pool X', 'pool TVL for address Y'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ids": {"type": "string", "description": "Pool ID(s) — single address or comma-separated list"},
            },
            "required": ["ids"],
        },
    },
    {
        "name": "raydium_pool_by_lp",
        "description": (
            "Raydium pool lookup by LP token mint — reverse-lookup from LP mint to pool details. "
            "Use for: 'which Raydium pool does this LP token belong to', 'pool info for LP mint X'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lps": {"type": "string", "description": "LP mint address(es) — single or comma-separated"},
            },
            "required": ["lps"],
        },
    },
    {
        "name": "raydium_pool_by_mint",
        "description": (
            "Find Raydium pools containing a specific token or token pair — search by mint address. "
            "Use for: 'Raydium pools for SOL', 'SOL/USDC pools on Raydium', 'all pools with BONK', "
            "'best Raydium pool for RAY/USDC', 'pools containing this token address'. "
            "mint1: required token. mint2: optional second token to find the specific pair."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mint1": {"type": "string", "description": "First token mint address (required)"},
                "mint2": {"type": "string", "description": "Second token mint address — narrows to a specific pair (optional)"},
                "poolType": {"type": "string", "description": "all | Standard | Concentrated | allFarm (default: all)"},
                "sortField": {"type": "string", "description": "volume24h | tvl | fee24h | apr24h | volume7d | apr7d (default: volume24h)"},
                "sortType": {"type": "string", "description": "asc | desc (default: desc)"},
                "limit": {"type": "integer", "description": "Number of results (default: 10)"},
            },
            "required": ["mint1"],
        },
    },
    {
        "name": "raydium_pool_keys",
        "description": (
            "Raydium pool SDK account keys — on-chain vault/config addresses needed for building transactions. "
            "Use for: 'pool keys for Raydium pool X', 'transaction accounts for Raydium pool', 'SDK keys'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ids": {"type": "string", "description": "Pool ID(s) — single or comma-separated"},
            },
            "required": ["ids"],
        },
    },
    {
        "name": "raydium_pool_liquidity_history",
        "description": (
            "Raydium pool TVL/liquidity history — time-series of pool liquidity changes. "
            "Use for: 'Raydium pool TVL chart', 'how has pool X liquidity changed', 'pool liquidity over time'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Pool ID"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "raydium_pool_position_history",
        "description": (
            "Raydium CLMM pool position history — price ticks and TVL data over time. "
            "Use for: 'CLMM pool price history', 'concentrated liquidity tick history for pool X'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "CLMM pool ID"},
            },
            "required": ["id"],
        },
    },
    # ── Raydium Mint ─────────────────────────────────────────────────────────
    {
        "name": "raydium_mint_list",
        "description": (
            "Raydium default/standard token list — verified tokens and block list. "
            "Use for: 'Raydium token list', 'which tokens are standard on Raydium', 'Raydium blocked tokens'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "raydium_mint_info",
        "description": (
            "Raydium token metadata by mint — symbol, name, decimals, logo, tags (hasFreeze, hasTransferFee), coingeckoId. "
            "Use for: 'Raydium token info for mint X', 'does token X have freeze authority on Raydium', "
            "'token decimals for Raydium swap'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mints": {"type": "string", "description": "Mint address(es) — single or comma-separated"},
            },
            "required": ["mints"],
        },
    },
    {
        "name": "raydium_mint_price",
        "description": (
            "Raydium v3 token prices — mint → USD price from Raydium API v3 (more accurate than v2). "
            "Use for: 'Raydium price for token X', 'prices of these tokens on Raydium', 'RAY/SOL/USDC Raydium price'. "
            "mints: comma-separated. Omit for all Raydium token prices."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mints": {"type": "string", "description": "Mint address(es) — comma-separated (optional, omit for all)"},
            },
        },
    },
    # ── Raydium Farms ─────────────────────────────────────────────────────────
    {
        "name": "raydium_farm_info",
        "description": (
            "Raydium farm pool info by farm ID — TVL, APR, LP price, reward tokens, tags. "
            "Use for: 'Raydium farm details for ID X', 'farm APR for this farm', 'Raydium yield farming info'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ids": {"type": "string", "description": "Farm pool ID(s) — single or comma-separated"},
            },
            "required": ["ids"],
        },
    },
    {
        "name": "raydium_farm_by_lp",
        "description": (
            "Find Raydium farms accepting a specific LP token — shows available farms, APR, and rewards for an LP mint. "
            "Use for: 'what farms accept this LP token', 'Raydium farming for SOL/USDC LP', 'farm APR for LP mint X'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lp": {"type": "string", "description": "LP token mint address"},
                "limit": {"type": "integer", "description": "Number of results (default: 20)"},
            },
            "required": ["lp"],
        },
    },
    {
        "name": "raydium_farm_keys",
        "description": (
            "Raydium farm SDK account keys — on-chain addresses for farm transactions. "
            "Use for: 'farm keys for Raydium farm X', 'stake/harvest transaction accounts'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ids": {"type": "string", "description": "Farm ID(s) — single or comma-separated"},
            },
            "required": ["ids"],
        },
    },
    # ── Raydium IDO ───────────────────────────────────────────────────────────
    {
        "name": "raydium_ido_keys",
        "description": (
            "Raydium LaunchLab IDO pool keys — programId, authority, project/buy vault addresses. "
            "Use for: 'IDO pool keys for Raydium LaunchLab', 'LaunchLab IDO account info'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ids": {"type": "string", "description": "IDO pool ID(s) — single or comma-separated"},
            },
            "required": ["ids"],
        },
    },
    # ── Raydium Main ──────────────────────────────────────────────────────────
    {
        "name": "raydium_info",
        "description": (
            "Raydium protocol overview — total TVL (USD) and 24-hour trading volume across all pools. "
            "Use for: 'Raydium TVL', 'Raydium total volume', 'Raydium protocol stats', 'how big is Raydium'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "raydium_auto_fee",
        "description": (
            "Raydium recommended priority fees — medium (m), high (h), very high (vh) in micro-lamports. "
            "Use for: 'what priority fee should I use on Raydium', 'Raydium transaction fee recommendation', "
            "'Raydium compute unit price'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "raydium_clmm_config",
        "description": (
            "Raydium CLMM fee tier configurations — all available concentrated liquidity fee tiers and tick spacings. "
            "Use for: 'Raydium CLMM fee tiers', 'available fee tiers for Raydium concentrated pools', "
            "'what tick spacings does Raydium support'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "raydium_cpmm_config",
        "description": (
            "Raydium CPMM fee tier configurations — available CPMM pool fee rates and pool creation fees. "
            "Use for: 'Raydium CPMM fee tiers', 'cost to create a Raydium CPMM pool', 'CPMM pool configs'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "raydium_rpcs",
        "description": (
            "Raydium recommended RPC endpoint list for UI clients. "
            "Use for: 'Raydium RPC endpoints', 'what RPC does Raydium use'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "raydium_chain_time",
        "description": (
            "Raydium chain time offset — millisecond difference between local UTC and Solana on-chain time. "
            "Use for: 'Raydium chain time', 'Solana clock offset'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "raydium_stake_pools",
        "description": (
            "Raydium RAY staking pools — list of stake pools with TVL, APR, and reward details. "
            "Use for: 'Raydium staking pools', 'RAY staking APR', 'stake RAY on Raydium'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "raydium_migrate_lp",
        "description": (
            "Raydium migrate LP list — AMM v4 pools eligible for migration to CLMM/V3. "
            "Use for: 'Raydium LP migration', 'which pools can migrate to CLMM', 'AMM to CLMM migration list'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "raydium_version",
        "description": (
            "Raydium API version — latest and minimum supported UI version. "
            "Use for: 'Raydium API version', 'what version is Raydium on'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    # ── Orca ─────────────────────────────────────────────────────────────────
    {
        "name": "orca_pools",
        "description": (
            "Orca Whirlpool (CLMM) paginated pool list — token pair, price, TVL, APR, fee tier, tick spacing. "
            "Use for: 'Orca pools', 'Orca liquidity', 'best Orca whirlpools', 'top pools by TVL'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of pools (default: 20)"},
                "sort_by": {"type": "string", "description": "Sort field e.g. tvl, volume (optional)"},
                "sort_dir": {"type": "string", "description": "asc or desc (optional)"},
            },
        },
    },
    {
        "name": "orca_pools_search",
        "description": (
            "Search Orca Whirlpool pools by token symbol or pool address. "
            "Use for: 'find SOL/USDC pool on Orca', 'search Orca pool by address'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Token symbol or pool address to search"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "orca_pool_by_address",
        "description": (
            "Detailed info for a specific Orca Whirlpool by its on-chain address — price, TVL, volume, fees, tick range. "
            "Use for: 'Orca pool details', 'info for this Orca pool address'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Whirlpool on-chain address"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "orca_locked_liquidity",
        "description": (
            "Locked liquidity info for a specific Orca Whirlpool — locked positions, amounts, unlock conditions. "
            "Use for: 'locked liquidity in this Orca pool', 'is Orca pool liquidity locked'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Whirlpool on-chain address"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "orca_protocol_stats",
        "description": (
            "Orca protocol-level statistics — total TVL, 24h volume, cumulative fees, revenue. "
            "Use for: 'Orca protocol TVL', 'Orca daily volume', 'Orca total fees'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "orca_protocol_token",
        "description": (
            "ORCA token detailed info — price, market cap, fully diluted valuation, supply, holders. "
            "Use for: 'ORCA token price', 'ORCA market cap', 'ORCA token info'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "orca_circulating_supply",
        "description": (
            "ORCA token current circulating supply. "
            "Use for: 'ORCA circulating supply', 'how many ORCA tokens are in circulation'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "orca_total_supply",
        "description": (
            "ORCA token total minted supply. "
            "Use for: 'ORCA total supply', 'total ORCA tokens minted'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "orca_tokens",
        "description": (
            "Paginated list of tokens available on Orca with metadata and pricing. "
            "Use for: 'Orca token list', 'which tokens are on Orca', 'Orca tradeable tokens'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of tokens (default: 20)"},
                "sort_by": {"type": "string", "description": "Sort field e.g. volume, tvl (optional)"},
                "sort_dir": {"type": "string", "description": "asc or desc (optional)"},
            },
        },
    },
    {
        "name": "orca_tokens_search",
        "description": (
            "Search Orca tokens by name, symbol, or mint address. "
            "Use for: 'find BONK on Orca', 'search Orca token by mint', 'is this token on Orca'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Token name, symbol, or mint address"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "orca_token_by_mint",
        "description": (
            "Detailed info for a specific token on Orca by its mint address — price, volume, pools. "
            "Use for: 'Orca token details by mint', 'token info on Orca'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mint_address": {"type": "string", "description": "Token mint address"},
            },
            "required": ["mint_address"],
        },
    },
    # ── Kamino ───────────────────────────────────────────────────────────────
    {
        "name": "kamino_strategies",
        "description": (
            "Kamino liquidity vault strategies — automated LP strategies with TVL, APY (7d/24h/30d), token pair. "
            "Use for: 'Kamino vaults', 'best Kamino yield', 'Kamino liquidity strategies', 'automated LP APY'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of strategies (default: 20)"},
            },
        },
    },
    {
        "name": "kamino_lending",
        "description": (
            "Kamino lending market config — primary market address on mainnet. "
            "Use for: 'Kamino lending', 'Kamino borrow/lend markets'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "kamino_markets",
        "description": (
            "All Kamino lending markets — name, isPrimary flag, lendingMarket pubkey, description. "
            "Returns 29 markets. Use for: 'list all Kamino markets', 'Kamino market addresses', 'which Kamino market is primary'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "kamino_market_reserves",
        "description": (
            "Kamino lending reserves for a market — token symbol, borrowApy, supplyApy, "
            "totalSupply, totalBorrow, totalSupplyUsd, totalBorrowUsd, maxLtv. "
            "Defaults to main market (55 reserves). "
            "Use for: 'Kamino borrow APY', 'Kamino supply APY', 'best Kamino lending rate', "
            "'Kamino TVL per reserve', 'what interest rate does Kamino pay', 'Kamino utilization'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "market": {
                    "type": "string",
                    "description": "Market pubkey (default: main market 7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF)",
                },
            },
        },
    },
    {
        "name": "kamino_oracle_prices",
        "description": (
            "Kamino oracle prices for all supported tokens — mint, name, price (USD), timestamp. "
            "Use for: 'Kamino token price', 'what price does Kamino use for X', 'oracle price on Kamino'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "kamino_earn_vaults",
        "description": (
            "Kamino Earn vault list — vault address, tokenMint, shares, performance/management fees. "
            "Use for: 'Kamino Earn vaults', 'Kamino yield vaults', 'list Kamino earn products'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max vaults to return (default: 30)"},
            },
        },
    },
    {
        "name": "kamino_staking_yields",
        "description": (
            "Kamino staking yields for ~41 LST tokens — tokenMint, apy. "
            "Use for: 'Kamino staking yield', 'LST APY on Kamino', 'best staking rate Kamino', "
            "'compare LST yields Kamino'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "kamino_staking_yields_mean",
        "description": (
            "Mean (average) staking yield per LST token on Kamino — tokenMint, apy. "
            "Use for: 'average LST APY', 'mean staking yield Kamino'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "kamino_leverage_stats",
        "description": (
            "Kamino leverage position stats — tvl, avgLeverage, totalBorrowedUsd, totalDepositedUsd, "
            "totalObligations per deposit/borrow reserve pair. "
            "Use for: 'Kamino leverage stats', 'average leverage on Kamino', 'Kamino leveraged positions TVL'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "market": {
                    "type": "string",
                    "description": "Market pubkey (default: main market)",
                },
            },
        },
    },
    {
        "name": "kamino_user_positions",
        "description": (
            "Kamino Earn vault positions for a wallet — vaultAddress, shares, tokenAmount, pnl. "
            "Use for: 'my Kamino Earn positions', 'wallet Kamino vaults', 'Kamino user portfolio'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Solana wallet public key"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "kamino_user_obligations",
        "description": (
            "Kamino lending obligations for a wallet — deposited collateral, borrowed amounts, health factor, LTV. "
            "Use for: 'my Kamino loan', 'Kamino borrow position', 'health factor on Kamino', 'liquidation risk Kamino'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Solana wallet public key"},
                "market": {"type": "string", "description": "Market pubkey (default: main)"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "kamino_epoch_info",
        "description": (
            "Current Solana epoch info from Kamino — epoch number, firstSlot, lastSlot, start/endBlockTime. "
            "Use for: 'current epoch', 'Solana epoch info', 'epoch timing'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    # ── Marinade ─────────────────────────────────────────────────────────────
    {
        "name": "marinade_stats",
        "description": (
            "Marinade Finance stats — mSOL/SOL and mSOL/USD exchange rates plus full TVL breakdown "
            "(staked_sol, total_usd, liquidity_sol, marinade_native_stake_sol, etc.). "
            "Use for: 'mSOL price', 'Marinade staking TVL', 'how much is mSOL worth in USD/SOL', 'Marinade total value locked'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "marinade_validators",
        "description": (
            "Marinade validators with full filtering/sorting — vote_account, activated_stake, score, rank, datacenter, country. "
            "Use for: 'Marinade validators', 'top Marinade validators by stake/credits', 'validators with Marinade stake', 'superminority validators Marinade'. "
            "order_field: Stake|Credits|MndeVotes. order_direction: ASC|DESC."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit":                {"type": "integer", "description": "Result count (default 50, max 100)"},
                "offset":               {"type": "integer", "description": "Pagination offset (default 0)"},
                "epochs":               {"type": "integer", "description": "Limit history size"},
                "query":                {"type": "string",  "description": "Filter by identity, vote_address, or info_name"},
                "query_identities":     {"type": "string",  "description": "Comma-separated identity list"},
                "query_superminority":  {"type": "boolean", "description": "Filter superminority validators only"},
                "query_marinade_score": {"type": "boolean", "description": "Filter validators with positive Marinade score"},
                "query_marinade_stake": {"type": "boolean", "description": "Filter validators that have Marinade stake"},
                "query_with_names":     {"type": "boolean", "description": "Filter validators that have an info_name"},
                "order_field":          {"type": "string",  "description": "Sort by: Stake, Credits, MndeVotes (default: Stake)"},
                "order_direction":      {"type": "string",  "description": "ASC or DESC (default: DESC)"},
            },
        },
    },
    {
        "name": "marinade_validator_scores",
        "description": (
            "Marinade validator scoring for all ~1700 validators — vote_account, score, rank, component_scores, eligible_stake_msol. "
            "Use for: 'Marinade validator scores', 'validator ranking Marinade', 'how is Marinade scoring validators'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of scores to return (default 100)"},
            },
        },
    },
    {
        "name": "marinade_score_breakdown",
        "description": (
            "Marinade detailed score breakdown — component scores and weights for all or a specific validator. "
            "Use for: 'Marinade score breakdown for validator X', 'why does this validator have a low Marinade score', 'validator component scores'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query_vote_account": {"type": "string", "description": "Vote account pubkey to filter (optional — omit for all)"},
            },
        },
    },
    {
        "name": "marinade_validator_uptimes",
        "description": (
            "Uptime history for a specific Marinade validator — status per epoch/slot. "
            "Use for: 'validator uptime on Marinade', 'how reliable is validator X', 'validator downtime history'. "
            "Requires identity (validator identity pubkey, not vote account — get from marinade_validators)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identity": {"type": "string", "description": "Validator identity pubkey"},
            },
            "required": ["identity"],
        },
    },
    {
        "name": "marinade_validator_commissions",
        "description": (
            "Commission change history for a specific Marinade validator — old_commission, new_commission, epoch. "
            "Use for: 'has validator X changed commission', 'commission history for validator', 'validator fee changes Marinade'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identity": {"type": "string", "description": "Validator identity pubkey"},
            },
            "required": ["identity"],
        },
    },
    {
        "name": "marinade_validator_versions",
        "description": (
            "Validator client version history for a specific Marinade validator — version string per epoch. "
            "Use for: 'which version is validator X running', 'validator software updates', 'Solana version history for validator'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identity": {"type": "string", "description": "Validator identity pubkey"},
            },
            "required": ["identity"],
        },
    },
    {
        "name": "marinade_cluster_stats",
        "description": (
            "Marinade cluster statistics — block production stats, datacenter concentration stats, current epoch. "
            "Use for: 'Solana cluster health', 'Marinade network stats', 'datacenter concentration Solana', 'block production stats'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "epochs": {"type": "integer", "description": "Limit history size (optional)"},
            },
        },
    },
    {
        "name": "marinade_epoch_rewards",
        "description": (
            "Marinade per-epoch rewards breakdown — rewards_mev, rewards_inflation_est, rewards_jito_priority, rewards_block per epoch. "
            "Use for: 'Marinade staking rewards', 'MEV rewards Marinade', 'how much did Marinade earn per epoch', 'Marinade inflation rewards'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "marinade_staking_report",
        "description": (
            "Marinade next-epoch delegation plan — planned stake changes per validator (current_stake, next_stake, vote_account). "
            "Use for: 'Marinade delegation plan', 'next epoch staking allocation', 'which validators get more/less stake from Marinade'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "marinade_scoring_reports",
        "description": (
            "Marinade scoring reports by epoch — scoring criteria, component weights, and results used in each epoch's delegation. "
            "Use for: 'Marinade scoring history', 'how did Marinade score validators this epoch', 'scoring report Marinade'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "marinade_commission_changes",
        "description": (
            "All validator commission change records tracked by Marinade — vote_account, old_commission, new_commission, epoch. "
            "Use for: 'which validators changed commission on Marinade', 'commission change history all validators', 'fee hikes Marinade'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "marinade_config",
        "description": (
            "Marinade system configuration — delegation authorities, program addresses, scoring parameters. "
            "Use for: 'Marinade program config', 'delegation authority addresses', 'Marinade protocol parameters'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "marinade_native_apy",
        "description": (
            "Marinade native staking historical APY — per-epoch apy_5_epochs, apy_10_epochs, apy_all_epochs time series. "
            "Use for: 'Marinade native staking APY', 'historical staking yield Marinade', 'native SOL staking returns Marinade'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of epochs to return (default 20)"},
            },
        },
    },
    {
        "name": "marinade_msol_apy",
        "description": (
            "mSOL APY for a specific time period — computed from mSOL/SOL price change over 7d, 30d, 1y, or 2y. "
            "Returns value (decimal, e.g. 0.069 = 6.9%), start/end price and timestamps. "
            "Use for: 'mSOL APY', 'mSOL yield 1 year', 'mSOL staking return 30 days', 'how much has mSOL grown'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["7d", "30d", "1y", "2y"],
                    "description": "Time window for APY calculation. Default '1y'.",
                },
            },
        },
    },
    {
        "name": "marinade_jito_commissions",
        "description": (
            "Marinade Jito validator commissions — vote_account, epoch, mev_commission_bps, priority_commission_bps for ~750 validators. "
            "Use for: 'Jito MEV commissions Marinade', 'validator MEV fees', 'priority commission validators Marinade', 'Jito tip commission'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of validators to return (default 100)"},
            },
        },
    },
    {
        "name": "marinade_msol_votes",
        "description": (
            "mSOL governance vote distribution — shows which validators mSOL holders are voting for. "
            "Aggregates 7000+ raw snapshot records into top N validators by total mSOL vote weight (voter count + mSOL amount). "
            "Use for: 'mSOL voting', 'which validators do mSOL holders vote for', 'mSOL governance', "
            "'mSOL delegation votes', 'who does mSOL community vote for', 'mSOL vote distribution'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "top_n": {"type": "integer", "description": "Number of top validators to return (default 20)"},
            },
        },
    },
    {
        "name": "marinade_vemnde_votes",
        "description": (
            "veMNDE governance vote distribution — shows which validators veMNDE holders are voting for. "
            "Aggregates raw snapshot records into top N validators by total veMNDE vote weight. "
            "Use for: 'veMNDE voting', 'veMNDE governance', 'MNDE vote distribution', "
            "'which validators veMNDE votes for', 'veMNDE delegation'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "top_n": {"type": "integer", "description": "Number of top validators to return (default 20)"},
            },
        },
    },
    {
        "name": "marinade_wallet_msol_balance",
        "description": (
            "Latest mSOL snapshot balance for a specific wallet address. "
            "Returns the mSOL amount held at last snapshot, the slot, and snapshot timestamp. "
            "Use for: 'my mSOL balance', 'how much mSOL does wallet X hold', 'mSOL snapshot for address'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Solana wallet public key (base58)"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "marinade_wallet_vemnde_balance",
        "description": (
            "Latest veMNDE snapshot balance for a specific wallet address. "
            "Returns the veMNDE amount held at last snapshot, the slot, and timestamp. "
            "Use for: 'my veMNDE balance', 'how much veMNDE does wallet X hold', 'veMNDE snapshot'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Solana wallet public key (base58)"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "marinade_wallet_native_stake",
        "description": (
            "Native stake balance history for a specific wallet over a date range (max 30 days). "
            "Returns time-series records of native stake amount for the wallet. "
            "Use for: 'my native stake history', 'how much SOL did I have staked', 'native stake over time for wallet X'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Solana wallet public key (base58)"},
                "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format (max 30 days from start)"},
            },
            "required": ["wallet", "start_date", "end_date"],
        },
    },
    # ── Solend ───────────────────────────────────────────────────────────────
    {
        "name": "solend_markets",
        "description": (
            "Solend/Save lending market configs — all markets, reserves, supported tokens, pool addresses. "
            "ids: optional comma-separated market IDs to filter. deployment: 'production' or 'devnet'. "
            "Use for: 'Solend markets', 'what can I lend on Solend', 'Save Finance pools'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ids": {"type": "string", "description": "Comma-separated market IDs to filter (optional)"},
                "scope": {"type": "string", "description": "'all' (default)"},
                "deployment": {"type": "string", "description": "'production' (default) or 'devnet'"},
            },
        },
    },
    {
        "name": "solend_reserves",
        "description": (
            "Solend reserve data — supply APY, borrow APY, LTV, liquidity, oracle price for each reserve. "
            "ids: optional comma-separated reserve pubkeys to filter specific reserves. "
            "Use for: 'Solend USDC reserve', 'SOL reserve APY on Solend', 'all Solend reserves'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "'all' (default)"},
                "ids": {"type": "string", "description": "Comma-separated reserve public keys to filter (optional)"},
            },
        },
    },
    {
        "name": "solend_user_overview",
        "description": (
            "Solend wallet/obligation overview — deposits, borrows, health factor across every market. "
            "Provide EITHER wallet OR obligation — not both. "
            "Use for: 'my Solend positions', 'Solend borrows', 'Solend health factor', 'what have I deposited on Solend'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Solana wallet public key — provide this OR obligation"},
                "obligation": {"type": "string", "description": "Obligation account public key — provide this OR wallet"},
            },
        },
    },
    {
        "name": "solend_stats",
        "description": (
            "Solend protocol-wide statistics — totalDepositsUSD, totalBorrowsUSD, totalMarkets, "
            "totalObligations, uniqueAssets, totalFeesUSD. "
            "Use for: 'Solend TVL', 'how big is Solend', 'Solend protocol stats'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "solend_daily_fees",
        "description": (
            "Solend fee revenue for a time period — origination fees, flash loan fees, spread fees. "
            "ts: Unix timestamp. span: time window e.g. '1d', '7d', '30d'. "
            "Use for: 'Solend fee revenue', 'how much did Solend earn', 'Solend protocol fees'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ts": {"type": "integer", "description": "Unix timestamp (start of period)"},
                "span": {"type": "string", "description": "Time window e.g. '1d', '7d', '30d'"},
                "dumpy_only": {"type": "boolean", "description": "Filter to dumpy-only fees (default: false)"},
            },
            "required": ["ts", "span"],
        },
    },
    {
        "name": "solend_lst_rates",
        "description": (
            "Solend LST (Liquid Staking Token) APR and APY rates — jitoSOL, mSOL, bSOL, sSOL and others. "
            "Keys are LST mint addresses; values contain apr and apy fields. "
            "Use for: 'Solend staking rates', 'LST APY on Solend', 'jitoSOL APR', 'mSOL yield'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "solend_prices",
        "description": (
            "Solend oracle prices for one or more tokens. "
            "Provide mints (comma-separated mint addresses) and/or symbols (comma-separated symbols like 'SOL,USDC'). "
            "Use for: 'Solend token price', 'price of SOL on Solend', 'USDC oracle price'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mints": {"type": "string", "description": "Comma-separated mint addresses (optional if symbols set)"},
                "symbols": {"type": "string", "description": "Comma-separated token symbols e.g. 'SOL,USDC' (optional if mints set)"},
            },
        },
    },
    {
        "name": "solend_historical_prices",
        "description": (
            "Solend historical oracle price for a token at specific Unix timestamps. "
            "Returns a map of timestamp → price. "
            "Use for: 'USDC price on Solend on date X', 'historical SOL price on Solend'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mint": {"type": "string", "description": "Token mint address"},
                "timestamps": {"type": "string", "description": "Comma-separated Unix timestamps e.g. '1700000000,1700100000'"},
            },
            "required": ["mint", "timestamps"],
        },
    },
    {
        "name": "solend_reserves_history",
        "description": (
            "Historical supply/borrow APY for specific Solend reserves over time. "
            "ids: comma-separated reserve public keys (get from solend_markets). "
            "span: '1w' (default), '30d', '90d', or '1y'. "
            "Returns map of reserveID → [{supplyAPY, borrowAPY, supplyAPR, borrowAPR, timestamp, cTokenExchangeRate}]. "
            "Use for: 'Solend APY history', 'USDC lending rate history on Solend', 'SOL borrow rate trend'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ids": {"type": "string", "description": "Comma-separated reserve public keys"},
                "span": {"type": "string", "description": "Time span: 1w (default), 30d, 90d, or 1y"},
            },
            "required": ["ids"],
        },
    },
    {
        "name": "solend_reserves_config_changes",
        "description": (
            "Solend reserve configuration change history — LTV changes, liquidation threshold changes, rate model updates. "
            "Use for: 'Solend parameter changes', 'when did Solend change LTV', 'reserve config history'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reserve_ids": {"type": "string", "description": "Comma-separated reserve public keys"},
                "market_id": {"type": "string", "description": "Lending market public key"},
            },
            "required": ["reserve_ids", "market_id"],
        },
    },
    {
        "name": "solend_daily_stats",
        "description": (
            "Solend daily protocol stats — totalBorrowers, totalDepositors, outstandingBorrowsUSD, interestGeneratedUSD. "
            "Defaults to today if no date given. Use for: 'Solend stats today', 'Solend stats on January 1', 'Solend historical data'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format. Omit for today's stats."},
            },
            "required": [],
        },
    },
    {
        "name": "solend_circulating_supply",
        "description": "Solend/SAVE token circulating supply. Use for: 'SAVE circulating supply', 'SLND supply'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "solend_total_supply",
        "description": "Solend/SAVE token total supply. Use for: 'SAVE total supply', 'SLND total tokens'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "solend_save_metrics",
        "description": (
            "SAVE token protocol metrics — totalFees, annualizedRevenue, savePrice. "
            "Use for: 'SAVE token metrics', 'Solend revenue', 'SAVE price', 'Solend annualized fees'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "solend_save_price_chart",
        "description": (
            "SAVE token price chart data over a time period. "
            "period: '1d', '7d', '30d', '90d', '1y', or 'all'. "
            "Use for: 'SAVE price chart', 'SLND price history', 'SAVE token performance'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "Time period: 1d, 7d, 30d, 90d, 1y, or all"},
            },
            "required": ["period"],
        },
    },
    {
        "name": "solend_save_revenue_chart",
        "description": (
            "SAVE protocol revenue chart data over a time period. "
            "period: '1d', '7d', '30d', '90d', '1y', or 'all'. "
            "Use for: 'Solend revenue chart', 'Save Finance revenue history', 'protocol earnings over time'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "Time period: 1d, 7d, 30d, 90d, 1y, or all"},
            },
            "required": ["period"],
        },
    },
    {
        "name": "solend_isolated_pool_stats",
        "description": (
            "Solend isolated pool statistics — TVL, borrow, utilization for all isolated lending pools. "
            "Use for: 'Solend isolated pools', 'isolated market stats', 'Solend pool utilization'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "solend_announcements",
        "description": (
            "Solend market announcements — news, updates, duyurular. "
            "id: optional market address (defaults to main market). "
            "Use for: 'Solend announcements', 'Solend news', 'Solend son duyuruları', 'Solend updates'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Market address (optional — omit for main market)"},
            },
            "required": [],
        },
    },
    {
        "name": "solend_changelogs",
        "description": (
            "Solend market changelog — protocol parameter changes, upgrades, config updates. "
            "id: optional market address (defaults to main market). "
            "Use for: 'Solend changelog', 'what changed on Solend', 'Solend version history'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Market address (optional — omit for main market)"},
            },
            "required": [],
        },
    },
    {
        "name": "solend_reward_stats",
        "description": (
            "Solend liquidity mining reward emission stats — reward APYs and emission rates per reserve across all markets. "
            "flat: if true, returns a flat list instead of nested by market. "
            "Use for: 'Solend farming rewards', 'Solend reward emissions', 'liquidity mining APY on Solend'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "flat": {"type": "boolean", "description": "Return flat list instead of nested by market (default: false)"},
            },
        },
    },
    {
        "name": "solend_reward_score",
        "description": (
            "Solend liquidity mining reward score for a wallet — determines reward share in distribution. "
            "Use for: 'my Solend reward score', 'Solend mining score', 'farming score for wallet'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Solana wallet public key (base58)"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "solend_reward_proofs",
        "description": (
            "Solend liquidity mining Merkle reward proofs for an obligation — needed for on-chain reward claims. "
            "Use for: 'Solend reward proof', 'claim proof for obligation', 'Solend Merkle proof'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "obligation": {"type": "string", "description": "Obligation account public key"},
            },
            "required": ["obligation"],
        },
    },
    {
        "name": "solend_additional_emissions",
        "description": (
            "Solend additional (supplemental) emission rates — extra reward APYs per reserve beyond base emissions. "
            "Use for: 'Solend bonus rewards', 'additional emissions', 'extra farming APY on Solend'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "solend_confirmed_rewards",
        "description": (
            "Confirmed (claimable) Solend liquidity mining rewards for a specific wallet. "
            "Use for: 'my Solend rewards', 'how much SLND can I claim', 'Solend farming earnings'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Solana wallet public key (base58)"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "solend_history",
        "description": (
            "Transaction history for a Solend obligation account — deposits, withdrawals, borrows, repayments. "
            "obligation: obligation account pubkey (not wallet address). "
            "Use for: 'my Solend transaction history', 'Solend activity log', 'when did I borrow on Solend'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "obligation": {"type": "string", "description": "Solend obligation account public key"},
            },
            "required": ["obligation"],
        },
    },
    {
        "name": "solend_history_v2",
        "description": (
            "Solend v2 transaction history for an obligation with slot-based pagination. "
            "max_slot: return transactions before this slot (omit for latest). "
            "Use for: 'Solend history paginated', 'Solend transactions before slot X'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "obligation": {"type": "string", "description": "Obligation account public key"},
                "max_slot": {"type": "integer", "description": "Return transactions before this slot (optional — omit for latest)"},
            },
            "required": ["obligation"],
        },
    },
    {
        "name": "solend_ctoken_history",
        "description": (
            "cToken (collateral token) deposit/withdraw history for a wallet across all Solend markets. "
            "Use for: 'my Solend cToken history', 'Solend collateral activity', 'when did I deposit to Solend'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Solana wallet public key (base58)"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "solend_liquidation_attempts",
        "description": (
            "Solend liquidation attempt history for a specific lending market. "
            "market: lending market public key (get from solend_markets). "
            "Use for: 'Solend liquidations', 'who got liquidated on Solend', 'liquidation history'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "market": {"type": "string", "description": "Lending market public key"},
            },
            "required": ["market"],
        },
    },
    {
        "name": "solend_margin_trading_history",
        "description": (
            "Solend margin trading transaction history — leveraged positions. "
            "Provide obligation, obligations (comma-separated), or wallet. max_slot for pagination. "
            "Use for: 'Solend margin trades', 'leveraged position history on Solend'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "obligation": {"type": "string", "description": "Single obligation pubkey (optional)"},
                "obligations": {"type": "string", "description": "Comma-separated obligation pubkeys (optional)"},
                "wallet": {"type": "string", "description": "Wallet pubkey (optional)"},
                "max_slot": {"type": "integer", "description": "Pagination cursor slot (optional)"},
            },
        },
    },
    {
        "name": "solend_snapshot",
        "description": (
            "Solend portfolio snapshot for a wallet at a specific Unix timestamp. "
            "Use for: 'my Solend portfolio on date X', 'Solend positions at timestamp', 'historical Solend snapshot'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Solana wallet public key (base58)"},
                "ts": {"type": "integer", "description": "Unix timestamp for the snapshot"},
            },
            "required": ["wallet", "ts"],
        },
    },
    {
        "name": "solend_transactions",
        "description": (
            "Solend transaction details by signature(s) — full transaction data and action type. "
            "signatures: comma-separated transaction signatures. "
            "Use for: 'Solend transaction detail', 'look up Solend tx signature'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "signatures": {"type": "string", "description": "Comma-separated transaction signatures"},
            },
            "required": ["signatures"],
        },
    },
    {
        "name": "solend_margin_trading_transactions",
        "description": (
            "Solend margin trading transaction details by signature(s). "
            "signatures: comma-separated transaction signatures. "
            "Use for: 'Solend margin trading tx', 'margin trading transaction detail on Solend'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "signatures": {"type": "string", "description": "Comma-separated transaction signatures"},
            },
            "required": ["signatures"],
        },
    },
    {
        "name": "solend_transaction_notes",
        "description": (
            "Human-readable labels/notes for Solend transactions by signature. "
            "signatures: comma-separated transaction signatures. "
            "Use for: 'what is this Solend transaction', 'Solend tx label'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "signatures": {"type": "string", "description": "Comma-separated transaction signatures"},
            },
            "required": ["signatures"],
        },
    },
    {
        "name": "solend_vip_eligibility",
        "description": (
            "Check if a wallet is eligible for Solend VIP status. Returns {eligible, verified}. "
            "Use for: 'am I Solend VIP', 'Solend VIP eligibility', 'Solend VIP check'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Solana wallet public key (base58)"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "solend_airdrops",
        "description": (
            "Solend airdrop information for a wallet — eligible amounts and claim status. "
            "Use for: 'Solend airdrop', 'do I have Solend airdrop', 'SLND airdrop check'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Solana wallet public key (base58)"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "solend_airdrops_jito",
        "description": (
            "Jito-specific airdrop information tracked by Solend for a wallet. "
            "Use for: 'Jito airdrop on Solend', 'jitoSOL airdrop eligibility'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Solana wallet public key (base58)"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "solend_obligations_filtered",
        "description": (
            "Search Solend obligations by filter criteria — useful for finding at-risk positions or whales. "
            "All params optional. min_deposit_usd/min_borrow_usd: filter by USD value. "
            "min_utilization: 0–1 ratio. deposit_assets/borrow_assets: comma-separated mints. "
            "market_address: specific market. limit/offset: pagination (default limit=20). "
            "Use for: 'at-risk Solend positions', 'large Solend borrowers', 'find undercollateralized obligations'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "min_deposit_usd": {"type": "string", "description": "Minimum deposit value in USD (default: '0')"},
                "min_borrow_usd": {"type": "string", "description": "Minimum borrow value in USD (default: '0')"},
                "min_utilization": {"type": "string", "description": "Minimum utilization ratio 0–1 (default: '0')"},
                "deposit_assets": {"type": "string", "description": "Comma-separated deposit mint addresses (optional)"},
                "borrow_assets": {"type": "string", "description": "Comma-separated borrow mint addresses (optional)"},
                "market_address": {"type": "string", "description": "Specific lending market pubkey (optional)"},
                "include_without_borrows": {"type": "string", "description": "'true' or 'false' (default: 'false')"},
                "limit": {"type": "string", "description": "Results per page (default: '20')"},
                "offset": {"type": "string", "description": "Pagination offset (default: '0')"},
            },
        },
    },
    {
        "name": "solend_squeezy_obligations",
        "description": (
            "Solend 'squeezy' obligations — positions at high liquidation risk for a given borrow asset. "
            "borrow_mint: mint address of the borrowed token. "
            "Use for: 'Solend liquidation risk', 'at-risk positions for USDC', 'who might get liquidated on Solend'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "borrow_mint": {"type": "string", "description": "Mint address of the borrowed token (e.g., USDC mint)"},
            },
            "required": ["borrow_mint"],
        },
    },
    {
        "name": "solend_notifications",
        "description": (
            "Solend in-app notifications for a wallet — protocol alerts, liquidation warnings, reward notices. "
            "Use for: 'my Solend notifications', 'Solend alerts for wallet', 'check Solend warnings'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Solana wallet public key (base58)"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "solend_tokens_all",
        "description": (
            "All tokens listed on Solend by deployment environment. "
            "Use for: 'what tokens are on Solend', 'Solend supported assets', 'list Solend tokens'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "'production' (default) or 'devnet'"},
            },
        },
    },
    {
        "name": "solend_tokens",
        "description": (
            "Solend token info filtered by mint addresses or symbols. "
            "deployment: 'production' (default) or 'devnet'. "
            "Use for: 'Solend token details for SOL', 'token config on Solend', 'find token on Solend by mint'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "'production' (default) or 'devnet'"},
                "mints": {"type": "string", "description": "Comma-separated mint addresses (optional)"},
                "symbols": {"type": "string", "description": "Comma-separated token symbols e.g. 'SOL,USDC' (optional)"},
            },
        },
    },
    {
        "name": "solend_referral_payments",
        "description": (
            "Solend referral payments overview — total referral fees and payment history. "
            "Use for: 'Solend referral program', 'Solend referral fees', 'referral payment history'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "solend_referral_attributed_payments",
        "description": (
            "Solend referral payments earned by a specific wallet as a referrer. "
            "Use for: 'how much did I earn from Solend referrals', 'my referral income on Solend'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Referrer wallet public key (base58)"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "solend_referral_referrer",
        "description": (
            "Look up who referred a given wallet on Solend. "
            "Use for: 'who referred this wallet on Solend', 'find referrer on Solend'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "referred_wallet": {"type": "string", "description": "Wallet whose referrer you want to look up (base58)"},
            },
            "required": ["referred_wallet"],
        },
    },
    {
        "name": "solend_referral_referred",
        "description": (
            "List wallets referred by a given referrer on Solend. "
            "Use for: 'which wallets did I refer on Solend', 'my referred users on Solend'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "referrer_wallet": {"type": "string", "description": "Referrer wallet public key (base58)"},
            },
            "required": ["referrer_wallet"],
        },
    },
    {
        "name": "solend_referral_stats",
        "description": (
            "Solend referral program aggregate stats — total referrers, referred wallets, total fees paid. "
            "Use for: 'Solend referral stats', 'how big is Solend referral program'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "solend_points",
        "description": (
            "Solend points overview — current season, multipliers, distribution info. "
            "Use for: 'Solend points', 'Solend rewards season', 'how does Solend points work'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "solend_points_leaderboard",
        "description": (
            "Solend points leaderboard — top wallets ranked by points earned. "
            "Use for: 'Solend points leaderboard', 'who has the most Solend points', 'Solend top users'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "solend_points_config",
        "description": (
            "Solend points system configuration — rules, asset weights, season details. "
            "Use for: 'how are Solend points calculated', 'Solend points rules', 'Solend points multipliers'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "solend_points_total",
        "description": (
            "Total points issued across all Solend users. "
            "Use for: 'total Solend points', 'Solend points supply'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "solend_points_adjustments",
        "description": (
            "Solend manual points adjustments — admin-issued bonus or penalty entries. "
            "Use for: 'Solend points adjustments', 'manual point corrections on Solend'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    # ── Jito (kobe direct — no auth) ─────────────────────────────────────────
    {
        "name": "jito_stake_pool_stats",
        "description": (
            "JitoSOL stake pool time-series stats — TVL, APY, circulating supply, active validator count per epoch. "
            "Use for: 'jitoSOL TVL history', 'Jito stake pool APY', 'how big is the Jito stake pool'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "jito_jitosol_ratio",
        "description": (
            "jitoSOL/SOL exchange rate history — how much SOL 1 jitoSOL is worth over time. "
            "Ratio grows as staking rewards accumulate. "
            "Use for: 'jitoSOL price history', 'jitoSOL staking rewards', 'jitoSOL/SOL ratio trend'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "jito_validators",
        "description": (
            "Jito MEV validators — vote account, identity, MEV commission (bps), and MEV rewards earned. "
            "Use for: 'Jito validator list', 'which validators run Jito', 'MEV commission by validator'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of validators (default: 20)"},
            },
        },
    },
    {
        "name": "jito_preferred_validators",
        "description": (
            "Top validators recommended for jitoSOL withdrawal — ranked by withdrawable stake. "
            "Use for: 'best validators to unstake from', 'jitoSOL withdrawal validators', 'preferred Jito validators'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of validators (default: 10)"},
            },
        },
    },
    # ── Jito (gateway) ────────────────────────────────────────────────────────
    {
        "name": "jito_tip_floor",
        "description": (
            "Jito MEV bundle tip floor — current P25/P50/P75/P95/P99 percentiles + EMA in SOL. "
            "Tells users how much to tip for Jito bundle inclusion. "
            "Use for: 'Jito tip', 'MEV fee', 'how much priority fee for Jito', 'bundle tip floor'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "jito_mev_rewards",
        "description": (
            "Current epoch total network MEV rewards and per-lamport reward rate for jitoSOL stakers. "
            "Use for: 'Jito MEV this epoch', 'jitoSOL rewards', 'Jito staking yield'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "jito_daily_mev",
        "description": (
            "Daily MEV rewards time series — tip count, total SOL tipped, unique tippers, validator tips per day. "
            "Use for: 'Jito MEV trend', 'how much MEV today', 'MEV rewards this week', 'last 7 days MEV'. "
            "Pass days=1 for today only, days=7 for this week, days=30 for this month (default)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of recent days to return. Use 1 for 'today', 7 for 'this week', 30 for 'this month'. Default: 30.",
                    "default": 30,
                },
            },
        },
    },
    {
        "name": "jito_stake_growth",
        "description": (
            "jitoSOL stake ratio by epoch — shows adoption growth of Jito liquid staking over time. "
            "Use for: 'jitoSOL growth', 'Jito staking trend', 'how much SOL is staked with Jito'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "jito_mev_commission",
        "description": (
            "Average MEV commission taken by Jito validators per epoch. "
            "Use for: 'Jito validator commission', 'MEV cut', 'net MEV yield after fees'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    # ── Helius ───────────────────────────────────────────────────────────────
    {
        "name": "helius_token_holders",
        "description": (
            "Top 20 token holder accounts (address + balance) for any SPL token via Helius RPC. "
            "Use for: 'who holds the most BONK', 'top holders of X', 'whale wallets'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mint": {"type": "string", "description": "Token mint address"},
            },
            "required": ["mint"],
        },
    },
    {
        "name": "helius_token_supply",
        "description": (
            "Total supply and decimals for any SPL token via Helius RPC. "
            "Use for: 'total BONK supply', 'how many tokens exist', 'circulating supply check'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mint": {"type": "string", "description": "Token mint address"},
            },
            "required": ["mint"],
        },
    },
    {
        "name": "helius_wallet_tokens",
        "description": (
            "All SPL token accounts in any Solana wallet via Helius RPC. "
            "Use for: 'what tokens does wallet X hold', 'check this wallet portfolio'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Solana wallet address"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "helius_wallet_txs",
        "description": (
            "Wallet transaction history via Helius Enhanced Transactions API. "
            "Returns decoded transactions with type (SWAP, TRANSFER, STAKE, NFT_SALE, etc.), "
            "source protocol (jupiter, raydium, orca, pumpfun, etc.), description, SOL value, fee. "
            "Use for: 'wallet activity', 'what swaps did this wallet do', 'wallet trading history', "
            "'did this address buy X', 'whale wallet activity'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Solana wallet address"},
                "limit": {"type": "integer", "description": "Number of transactions (default: 20, max: 50)"},
            },
            "required": ["wallet"],
        },
    },
    # ── Security ─────────────────────────────────────────────────────────────
    {
        "name": "token_security",
        "description": (
            "Token risk/security check via rugcheck — mint authority status, freeze authority, "
            "LP lock percentage, risk score (0–100, lower = riskier), and specific risk flags. "
            "Use for: 'is X safe', 'rug pull risk', 'token security check', 'mint authority check'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mint": {"type": "string", "description": "Token mint address"},
            },
            "required": ["mint"],
        },
    },
    # ── User portfolio ────────────────────────────────────────────────────────
    {
        "name": "wallet_balance",
        "description": (
            "Authenticated user's wallet token balances — SOL + all SPL tokens. "
            "Use for: 'my wallet', 'my portfolio', 'how much SOL do I have'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "user_transactions",
        "description": (
            "Authenticated user's OPRAI transaction history — swaps, transfers, stakes via OPRAI. "
            "Use for: 'my transactions', 'what did I trade', 'my swap history'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "limit_orders",
        "description": (
            "User's Jupiter limit orders. status: open (default) or closed. "
            "Use for: 'my limit orders', 'open orders', 'pending trades'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "open or closed (default: open)"},
            },
        },
    },
    {
        "name": "dca_orders",
        "description": (
            "User's Jupiter DCA (Dollar Cost Averaging) orders. status: active or cancelled. "
            "Use for: 'my DCA', 'auto-buy schedule', 'recurring purchase orders'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "active or cancelled (default: active)"},
            },
        },
    },
    # ── Birdeye ──────────────────────────────────────────────────────────────
    {
        "name": "birdeye_price",
        "description": (
            "Real-time USD price for a single token from Birdeye — aggregates all Solana DEXes. "
            "Returns: value, updateUnixTime, priceChange24h. Does NOT return a liquidity field. "
            "check_liquidity=true is a price-quality filter only (excludes illiquid DEX pools from "
            "price aggregation — it does NOT add liquidity data to the response). "
            "More accurate than Jupiter for long-tail tokens. "
            "Use for: 'Birdeye price of X', 'exact current price'. "
            "For liquidity data alongside price, ALSO call birdeye_token_market_data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address"},
                "check_liquidity": {"type": "boolean", "description": "Price-quality filter: excludes illiquid DEX pools from aggregation. Does NOT return a liquidity field. Default: false."},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_multi_price",
        "description": (
            "Real-time USD prices for multiple tokens from Birdeye in a single call. "
            "Returns per-token: value, updateUnixTime, priceChange24h, priceInNative, isScaledUiToken. "
            "Accepts up to 100 addresses. Supports multi-chain (solana, ethereum, etc.). "
            "include_liquidity=true adds a 'liquidity' (USD float) field per token — use this to get liquidity alongside price. "
            "check_liquidity=N (integer USD threshold) is a price-QUALITY filter only: excludes tokens whose "
            "liquidity is below N USD from aggregation — it does NOT add a liquidity field. "
            "ui_amount_mode: 'raw' (default), 'scaled' (for scaled-UI tokens only), 'both'. "
            "Use for: 'compare prices of SOL, BONK, WIF', 'batch price lookup', 'portfolio snapshot', "
            "'prices + liquidity for multiple tokens'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "list_address": {"type": "string", "description": "Comma-separated token mint addresses (max 100)"},
                "check_liquidity": {"type": "integer", "description": "Min USD liquidity threshold for price-quality filter (e.g. 1000). Quality filter only — does NOT add liquidity field. Omit if not needed."},
                "include_liquidity": {"type": "boolean", "description": "If true, adds 'liquidity' (USD float) field per token. Use when caller wants liquidity alongside price."},
                "ui_amount_mode": {"type": "string", "enum": ["raw", "scaled", "both"], "description": "Amount mode: 'raw' (default), 'scaled' (for scaled-UI tokens), 'both'."},
                "chain": {"type": "string", "description": "Blockchain to query. Default: 'solana'. Other options: 'ethereum', 'bsc', 'polygon', etc."},
            },
            "required": ["list_address"],
        },
    },
    {
        "name": "birdeye_ohlcv",
        "description": (
            "OHLCV (open/high/low/close/volume) candlestick data for a token from Birdeye v3. "
            "Use to analyse price trends, identify support/resistance, check volatility, get trading volume. "
            "Per-candle response: o (open), c (close), h (high), l (low), v (volume in tokens), v_usd (volume USD), unix_time. "
            "type options: 1s 15s 30s 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M. "
            "Two modes: "
            "  mode=range (default): requires BOTH time_from AND time_to Unix timestamps. "
            "  mode=count: requires count_limit (max 5000 candles) and only ONE of time_from or time_to "
            "              (time_from = count forward from that time; time_to = count backward from that time). "
            "currency: usd (default) or native (returns price in the chain's native token). "
            "padding=true fills empty candles (no trades) with previous close. "
            "outlier=false removes outlier candles (default: true = include all). "
            "Use for: 'SOL price chart', 'BONK hourly candles', 'last 10 daily candles', "
            "'was price higher yesterday', 'weekly chart for WIF', 'volume today'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address"},
                "type": {"type": "string", "description": "Candle interval: 1s 15s 30s 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M (default: 1D)"},
                "time_from": {"type": "integer", "description": "Start Unix timestamp (seconds). Required for mode=range. For mode=count: anchor point (counts forward if provided alongside count_limit)."},
                "time_to": {"type": "integer", "description": "End Unix timestamp (seconds). Required for mode=range. For mode=count: anchor point (counts backward if provided alongside count_limit)."},
                "currency": {"type": "string", "enum": ["usd", "native"], "description": "Price currency: usd (default) or native (chain's native token)."},
                "ui_amount_mode": {"type": "string", "enum": ["raw", "scaled", "both"], "description": "Amount mode for scaled-UI tokens. Default: raw."},
                "mode": {"type": "string", "enum": ["range", "count"], "description": "range (default, needs time_from+time_to) or count (needs count_limit + only one of time_from/time_to)."},
                "count_limit": {"type": "integer", "description": "Number of candles to return when mode=count (max 5000)."},
                "padding": {"type": "boolean", "description": "If true, fills empty intervals (no trades) with previous close. Default: false."},
                "outlier": {"type": "boolean", "description": "If false, removes outlier candles. Default: true (include all)."},
                "chain": {"type": "string", "description": "Blockchain: solana (default), ethereum, bsc, base, monad."},
            },
            "required": ["address", "type"],
        },
    },
    {
        "name": "birdeye_ohlcv_pair",
        "description": (
            "OHLCV candlestick data for a specific DEX pool/pair address from Birdeye v3. "
            "Use when the caller provides a pair/pool contract address (not a token mint). "
            "Per-candle response: o, c, h, l, v (token vol), v_usd, unix_time, symbol (e.g. 'SOL-USDC'). "
            "type options: 1s 15s 30s 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M. "
            "mode=range (default): needs BOTH time_from AND time_to. "
            "mode=count: needs count_limit and only ONE of time_from or time_to. "
            "inversion=true: inverts base/quote direction (e.g. USDC-per-SOL instead of SOL-per-USDC). "
            "Use for: 'candles for this Raydium/Orca pool', 'OHLCV by pair address', "
            "'inverted pair chart', 'price of USDC in SOL terms'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "DEX pool/pair contract address"},
                "type": {"type": "string", "description": "Candle interval: 1s 15s 30s 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M (default: 1D)"},
                "time_from": {"type": "integer", "description": "Start Unix timestamp (seconds). Required for mode=range. For mode=count: counts forward from here."},
                "time_to": {"type": "integer", "description": "End Unix timestamp (seconds). Required for mode=range. For mode=count: counts backward from here."},
                "mode": {"type": "string", "enum": ["range", "count"], "description": "range (default, needs time_from+time_to) or count (needs count_limit + one of time_from/time_to)."},
                "count_limit": {"type": "integer", "description": "Number of candles when mode=count (max 5000)."},
                "padding": {"type": "boolean", "description": "Fill empty intervals with previous close. Default: false."},
                "outlier": {"type": "boolean", "description": "Include outlier candles. Default: true."},
                "inversion": {"type": "boolean", "description": "Invert base/quote direction. Default: false."},
                "chain": {"type": "string", "description": "Blockchain: solana (default), ethereum, bsc, base, monad."},
            },
            "required": ["address", "type"],
        },
    },
    {
        "name": "birdeye_ohlcv_v1",
        "description": (
            "Legacy (v1) OHLCV candlestick data for a token from Birdeye /defi/ohlcv. "
            "Simpler than birdeye_ohlcv (no mode/count_limit/padding/outlier) but supports 16+ chains. "
            "Per-candle response: o, c, h, l, v (volume in token), unixTime, currency, type. "
            "type options: 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M (no sub-minute). "
            "time_from and time_to are required. Max 1000 records per call. "
            "Extra chains vs v3: arbitrum, avalanche, optimism, polygon, zksync, sui, and others. "
            "Use when: querying chains not supported by birdeye_ohlcv (e.g. Arbitrum, Polygon), "
            "or when simplicity is preferred over mode/count features."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token contract address"},
                "type": {"type": "string", "description": "Candle interval: 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M (default: 1D)"},
                "time_from": {"type": "integer", "description": "Start Unix timestamp in seconds (required)"},
                "time_to": {"type": "integer", "description": "End Unix timestamp in seconds (required)"},
                "currency": {"type": "string", "enum": ["usd", "native"], "description": "Price currency: usd (default) or native."},
                "ui_amount_mode": {"type": "string", "enum": ["raw", "scaled", "both"], "description": "Amount mode. Default: raw. Solana only."},
                "chain": {"type": "string", "description": "Blockchain: solana (default), ethereum, arbitrum, avalanche, optimism, polygon, bsc, base, zksync, etc."},
            },
            "required": ["address", "type", "time_from", "time_to"],
        },
    },
    {
        "name": "birdeye_ohlcv_pair_v1",
        "description": (
            "Legacy (v1) OHLCV candlestick data for a DEX pair/pool address from Birdeye /defi/ohlcv/pair. "
            "Takes a pair contract address (not a token address). "
            "Simpler than birdeye_ohlcv_pair (no mode/count_limit/padding/outlier/inversion) but supports 16+ chains. "
            "Per-candle response: o, c, h, l, v, unixTime. Max 1000 records per call. "
            "type options: 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M. "
            "time_from and time_to are required. "
            "Extra chains vs v3: arbitrum, avalanche, optimism, polygon, zksync, sui, aptos, etc. "
            "Use when: querying pair candles on chains not supported by birdeye_ohlcv_pair, "
            "or when simplicity is preferred over mode/count features."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "DEX pair/pool contract address"},
                "type": {"type": "string", "description": "Candle interval: 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M (default: 1D)"},
                "time_from": {"type": "integer", "description": "Start Unix timestamp in seconds (required)"},
                "time_to": {"type": "integer", "description": "End Unix timestamp in seconds (required)"},
                "chain": {"type": "string", "description": "Blockchain: solana (default), ethereum, arbitrum, avalanche, optimism, polygon, bsc, base, zksync, etc."},
            },
            "required": ["address", "type", "time_from", "time_to"],
        },
    },
    {
        "name": "birdeye_ohlcv_base_quote",
        "description": (
            "OHLCV candlestick data for a custom pair defined by base and quote token addresses (no pool address needed). "
            "Useful when you want a custom pair like BONK/SOL or WIF/USDC. "
            "Per-candle response: o, c, h, l, v, unixTime. Max 1000 records per call. "
            "type options: 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M. "
            "time_from and time_to required. "
            "Use for: 'WIF priced in SOL chart', 'BONK/USDC candles', 'token X vs token Y chart', "
            "'price of X in terms of Y'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "base_address": {"type": "string", "description": "Base token mint address (the token being priced)"},
                "quote_address": {"type": "string", "description": "Quote token mint address (the currency to price in)"},
                "type": {"type": "string", "description": "Candle interval: 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M (default: 1D)"},
                "time_from": {"type": "integer", "description": "Start Unix timestamp in seconds (required)"},
                "time_to": {"type": "integer", "description": "End Unix timestamp in seconds (required)"},
                "ui_amount_mode": {"type": "string", "enum": ["raw", "scaled", "both"], "description": "Amount mode. Default: raw. Solana only."},
                "chain": {"type": "string", "description": "Blockchain: solana (default), ethereum, arbitrum, avalanche, optimism, polygon, bsc, base, zksync, etc."},
            },
            "required": ["base_address", "quote_address", "type", "time_from", "time_to"],
        },
    },
    {
        "name": "birdeye_history_price",
        "description": (
            "Continuous close-price timeline for a token or pair from Birdeye — good for smooth price curves and ROI. "
            "Returns {unixTime, value} data points (not OHLCV candles). Max 1000 points per call. "
            "address_type: 'token' (default) or 'pair'. "
            "type options: 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M. "
            "time_from and time_to required. "
            "Supports 16+ chains via chain param. "
            "Use for: 'price history last 30 days', 'how much has SOL changed this month', "
            "'line chart data', 'what was the price between date A and date B', "
            "'ROI since X date', 'price trend over time'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint or pair address"},
                "address_type": {"type": "string", "enum": ["token", "pair"], "description": "token (default) or pair"},
                "type": {"type": "string", "description": "Data point interval: 1m 3m 5m 15m 30m 1H 2H 4H 6H 8H 12H 1D 3D 1W 1M (default: 1D)"},
                "time_from": {"type": "integer", "description": "Start Unix timestamp in seconds (required)"},
                "time_to": {"type": "integer", "description": "End Unix timestamp in seconds (required)"},
                "ui_amount_mode": {"type": "string", "enum": ["raw", "scaled", "both"], "description": "Amount mode. Default: raw. Solana only."},
                "chain": {"type": "string", "description": "Blockchain: solana (default), ethereum, arbitrum, avalanche, optimism, polygon, bsc, base, zksync, etc."},
            },
            "required": ["address", "address_type", "type", "time_from", "time_to"],
        },
    },
    {
        "name": "birdeye_price_at_time",
        "description": (
            "Price of a Solana token at a specific past Unix timestamp from Birdeye. "
            "Solana only (not multi-chain). "
            "Use to answer 'what was the price on a specific date/time' questions precisely. "
            "Convert dates to Unix timestamps before calling (e.g. Jan 1 2025 00:00 UTC = 1735689600). "
            "unixtime is optional — omit for the current price. "
            "Use for: 'what was SOL price on Jan 1 2025', 'price at timestamp X', "
            "'how much was BONK worth last month on the 15th'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address (Solana only)"},
                "unixtime": {"type": "integer", "description": "Target Unix timestamp in seconds (optional — omit for current price)"},
                "ui_amount_mode": {"type": "string", "enum": ["raw", "scaled", "both"], "description": "Amount mode. Default: raw."},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_price_volume",
        "description": (
            "Price and trading volume statistics for a single token from Birdeye over a time window. "
            "Returns: current price, total volume, price change %, buy volume, sell volume. "
            "type: 1h, 2h, 4h, 8h, 24h (default). "
            "Use for: 'SOL trading volume today', 'BONK volume last hour', "
            "'buy vs sell pressure for X', '4-hour volume snapshot'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address"},
                "type": {"type": "string", "enum": ["1h", "2h", "4h", "8h", "24h"], "description": "Time window: 1h, 2h, 4h, 8h, 24h (default: 24h)"},
                "ui_amount_mode": {"type": "string", "enum": ["raw", "scaled", "both"], "description": "Amount mode. Default: raw."},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_price_volume_multi",
        "description": (
            "Price and trading volume statistics for multiple tokens from Birdeye in one call. "
            "Returns per-token: price, total volume, price change %, buy/sell breakdown. "
            "type: 1h, 2h, 4h, 8h, 24h (default). "
            "Use for: 'compare volume of SOL vs BONK vs WIF', 'volume leaders among a list of tokens', "
            "'batch price+volume snapshot'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "list_address": {"type": "string", "description": "Comma-separated token mint addresses"},
                "type": {"type": "string", "enum": ["1h", "2h", "4h", "8h", "24h"], "description": "Time window: 1h, 2h, 4h, 8h, 24h (default: 24h)"},
            },
            "required": ["list_address"],
        },
    },
    {
        "name": "birdeye_token_overview",
        "description": (
            "Comprehensive token overview from Birdeye — aggregates price, volume, liquidity, "
            "market cap, holder stats, and multi-frame trading data in one call. "
            "The most complete single-token data endpoint Birdeye offers. "
            "frames: comma-separated custom intervals e.g. '1h,4h,24h' (up to 8 frames; "
            "options: 1m–1440m, 5s–3600s in 5s steps, 1h 2h 4h 8h 24h). "
            "Supports 16+ chains via chain param. "
            "Use for: 'full breakdown of token X', 'all stats for BONK', "
            "'deep analysis with multiple timeframes', 'token overview dashboard data'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address"},
                "frames": {"type": "string", "description": "Comma-separated time intervals e.g. '1h,4h,24h' (up to 8, optional)"},
                "ui_amount_mode": {"type": "string", "enum": ["raw", "scaled"], "description": "raw or scaled (Solana only, default: scaled)"},
                "chain": {"type": "string", "description": "Blockchain: solana (default), ethereum, arbitrum, avalanche, optimism, polygon, bsc, base, zksync, etc."},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_token_metadata",
        "description": (
            "Token metadata from Birdeye v3 — name, symbol, decimals, logo URI, description, "
            "website, Twitter, Telegram, coingecko ID, and extension fields. "
            "Supports 16+ chains via chain param. "
            "Use for: 'token info', 'BONK logo and social links', 'official token description', "
            "'is this the real WIF token', 'token website/twitter', 'token name/symbol for address'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address"},
                "chain": {"type": "string", "description": "Blockchain: solana (default), ethereum, arbitrum, avalanche, optimism, polygon, bsc, base, zksync, etc."},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_token_metadata_multi",
        "description": (
            "Token metadata for multiple tokens from Birdeye v3 in one call (max 50). "
            "Returns name, symbol, decimals, logo, socials per token. "
            "Supports 16+ chains via chain param. "
            "Use for: 'info on these tokens', 'batch metadata lookup', 'compare token socials', "
            "'names/symbols for a list of addresses'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "list_address": {"type": "string", "description": "Comma-separated token mint addresses (max 50)"},
                "chain": {"type": "string", "description": "Blockchain: solana (default), ethereum, arbitrum, avalanche, optimism, polygon, bsc, base, zksync, etc."},
            },
            "required": ["list_address"],
        },
    },
    {
        "name": "birdeye_token_market_data",
        "description": (
            "Token market data from Birdeye v3 — price, market cap, FDV (fully diluted value), "
            "circulating supply, total supply, liquidity, and 24h volume. "
            "Supports 16+ chains via chain param. "
            "Use for: 'BONK market cap', 'FDV vs market cap ratio', 'circulating supply', "
            "'is token fully diluted', 'market data for X'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address"},
                "ui_amount_mode": {"type": "string", "enum": ["raw", "scaled"], "description": "raw or scaled (default: scaled, Solana only)"},
                "chain": {"type": "string", "description": "Blockchain: solana (default), ethereum, arbitrum, avalanche, optimism, polygon, bsc, base, zksync, etc."},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_token_market_data_multi",
        "description": (
            "Token market data for multiple tokens from Birdeye v3 in one call (max 20). "
            "Returns price, market cap, FDV, supply, liquidity, volume per token. "
            "Supports 16+ chains via chain param. "
            "Use for: 'compare market caps of SOL, JUP, RAY', 'batch market data', "
            "'which token has the highest FDV in this list'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "list_address": {"type": "string", "description": "Comma-separated token mint addresses (max 20)"},
                "ui_amount_mode": {"type": "string", "enum": ["raw", "scaled"], "description": "raw or scaled (default: scaled)"},
                "chain": {"type": "string", "description": "Blockchain: solana (default), ethereum, arbitrum, avalanche, optimism, polygon, bsc, base, zksync, etc."},
            },
            "required": ["list_address"],
        },
    },
    {
        "name": "birdeye_token_trade_data",
        "description": (
            "Token trade data from Birdeye v3 — buy/sell counts, buy/sell volume, "
            "unique buyers/sellers across custom time frames. Ideal for momentum and flow analysis. "
            "frames: comma-separated intervals e.g. '1h,4h,24h' (up to 8; "
            "options: 1m–1440m, 1h 2h 4h 8h 24h). "
            "Use for: 'BONK trade count last hour', 'buy vs sell activity', "
            "'how many unique buyers today', 'trading momentum across timeframes'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address"},
                "frames": {"type": "string", "description": "Comma-separated intervals e.g. '1h,4h,24h' (up to 8, optional)"},
                "ui_amount_mode": {"type": "string", "description": "raw or scaled (default: scaled, optional)"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_token_trade_data_multi",
        "description": (
            "Token trade data for multiple tokens from Birdeye v3 — buy/sell counts, volume, unique traders. "
            "frames: comma-separated intervals (up to 8). "
            "Use for: 'compare trading activity of SOL vs BONK', 'which token has more buyers today', "
            "'batch trade flow analysis'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "list_address": {"type": "string", "description": "Comma-separated token mint addresses"},
                "frames": {"type": "string", "description": "Comma-separated intervals e.g. '1h,24h' (up to 8, optional)"},
                "ui_amount_mode": {"type": "string", "description": "raw or scaled (default: scaled, optional)"},
            },
            "required": ["list_address"],
        },
    },
    {
        "name": "birdeye_exit_liquidity",
        "description": (
            "Exit liquidity (in USD and native tokens) for a token on the Base (Ethereum L2) network. "
            "Shows how much liquidity is available to sell a position. "
            "NOTE: Base chain only — do NOT use for Solana tokens. "
            "Use for: 'how much can I sell of this Base token', 'exit liquidity for Base token address X'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token contract address on Base chain"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_exit_liquidity_multi",
        "description": (
            "Exit liquidity for multiple tokens on the Base (Ethereum L2) network (max 50). "
            "NOTE: Base chain only — do NOT use for Solana tokens. "
            "Use for: 'compare exit liquidity of these Base tokens', 'batch Base liquidity check'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "list_address": {"type": "string", "description": "Comma-separated Base chain token contract addresses (max 50)"},
            },
            "required": ["list_address"],
        },
    },
    {
        "name": "birdeye_pair_overview",
        "description": (
            "Comprehensive DEX trading pair overview from Birdeye v3 — base/quote token info, "
            "current price, 24h volume, TVL, liquidity, fee tier, DEX name, and price changes. "
            "Pass the pool/pair address (not a token address). "
            "Use for: 'full data on this Raydium pool', 'pair stats for this address', "
            "'pool overview', 'what tokens are in this pool', 'TVL and volume for specific pair'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "DEX pair/pool contract address"},
                "ui_amount_mode": {"type": "string", "enum": ["raw", "scaled"], "description": "raw or scaled (default: scaled, optional)"},
                "chain": {"type": "string", "description": "Blockchain: solana (default), ethereum, arbitrum, avalanche, optimism, polygon, bsc, base, zksync, etc."},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_pair_overview_multi",
        "description": (
            "Comprehensive DEX trading pair overview for multiple pairs from Birdeye v3 (max 20). "
            "Returns base/quote info, price, volume, TVL, fee tier per pair. Supports 16+ chains. "
            "Use for: 'compare these pools', 'batch pair overview', 'best pool among these addresses'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "list_address": {"type": "string", "description": "Comma-separated DEX pair/pool addresses (max 20)"},
                "ui_amount_mode": {"type": "string", "enum": ["raw", "scaled"], "description": "raw or scaled (default: scaled, optional)"},
                "chain": {"type": "string", "description": "Blockchain: solana (default), ethereum, arbitrum, avalanche, optimism, polygon, bsc, base, zksync, etc."},
            },
            "required": ["list_address"],
        },
    },
    {
        "name": "birdeye_price_stats",
        "description": (
            "Price statistics for a token across multiple timeframes from Birdeye v3. "
            "Returns per-timeframe: current price, high, low, price change %. "
            "list_timeframe: comma-separated e.g. '1h,4h,24h,7d' "
            "(options: 1m 5m 30m 1h 2h 4h 8h 24h 2d 3d 7d). "
            "Use for: 'SOL price performance across timeframes', 'is BONK up on all timeframes', "
            "'multi-timeframe analysis', '1h vs 24h vs 7d change', 'technical price summary'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address"},
                "list_timeframe": {"type": "string", "description": "Comma-separated timeframes e.g. '1h,4h,24h,7d' (optional, returns all if omitted)"},
                "ui_amount_mode": {"type": "string", "description": "raw, scaled, or both (default: raw, optional)"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_price_stats_multi",
        "description": (
            "Price statistics for multiple tokens across multiple timeframes from Birdeye v3. "
            "Returns per-token per-timeframe: high, low, price change %. "
            "list_timeframe: comma-separated e.g. '1h,24h,7d' (options: 1m 5m 30m 1h 2h 4h 8h 24h 2d 3d 7d). "
            "Use for: 'compare SOL, BONK, WIF performance across timeframes', "
            "'batch multi-timeframe analysis', 'which token is strongest on 7d'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "list_address": {"type": "string", "description": "Comma-separated token mint addresses"},
                "list_timeframe": {"type": "string", "description": "Comma-separated timeframes e.g. '1h,24h,7d' (optional)"},
                "ui_amount_mode": {"type": "string", "description": "raw, scaled, or both (default: raw, optional)"},
            },
            "required": ["list_address"],
        },
    },
    {
        "name": "birdeye_token_list",
        "description": (
            "Filtered and sorted token list from Birdeye v3 — the most powerful token screener endpoint. "
            "Sort by any metric and apply min/max filters on liquidity, market cap, FDV, holders. "
            "sort_by options: liquidity | market_cap | fdv | recent_listing_time | last_trade_unix_time "
            "| holder | volume_1h_usd | volume_24h_usd | volume_7d_usd "
            "| price_change_1h_percent | price_change_24h_percent | price_change_7d_percent "
            "| trade_1h_count | trade_24h_count. "
            "sort_type: desc (default) or asc. limit: max 100. "
            "Use for: 'top tokens by volume', 'highest liquidity tokens', 'tokens with most holders', "
            "'biggest gainers today', 'tokens with market cap over $10M', 'token screener / scanner'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sort_by": {"type": "string", "description": "Sort field (default: market_cap). Options: liquidity, market_cap, fdv, recent_listing_time, last_trade_unix_time, holder, volume_1h_usd, volume_24h_usd, volume_7d_usd, price_change_1h_percent, price_change_24h_percent, price_change_7d_percent, trade_1h_count, trade_24h_count"},
                "sort_type": {"type": "string", "description": "desc (default) or asc"},
                "min_liquidity": {"type": "number", "description": "Minimum liquidity in USD (optional)"},
                "max_liquidity": {"type": "number", "description": "Maximum liquidity in USD (optional)"},
                "min_market_cap": {"type": "number", "description": "Minimum market cap in USD (optional)"},
                "max_market_cap": {"type": "number", "description": "Maximum market cap in USD (optional)"},
                "min_fdv": {"type": "number", "description": "Minimum fully diluted valuation in USD (optional)"},
                "max_fdv": {"type": "number", "description": "Maximum FDV in USD (optional)"},
                "min_holder": {"type": "integer", "description": "Minimum holder count (optional)"},
                "offset": {"type": "integer", "description": "Pagination offset 0-10000 (optional)"},
                "limit": {"type": "integer", "description": "Results per page 1-100 (default: 50)"},
            },
        },
    },
    {
        "name": "birdeye_token_list_scroll",
        "description": (
            "Large paginated token list from Birdeye v3 using scroll-based pagination (up to 5000 tokens per page). "
            "Supports all the same sort/filter options as birdeye_token_list plus additional time and volume filters. "
            "Pass scroll_id from the previous response to fetch the next page. "
            "NOTE: Rate-limited to 1 request per 30 seconds per scroll_id. "
            "Use for: 'scan all tokens with >$1M liquidity', 'full market scan', "
            "'fetch large dataset for analysis', 'paginate through many tokens'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sort_by": {"type": "string", "description": "Sort field (default: market_cap). Same options as birdeye_token_list"},
                "sort_type": {"type": "string", "description": "desc (default) or asc"},
                "scroll_id": {"type": "string", "description": "Scroll cursor from previous response for pagination (optional)"},
                "limit": {"type": "integer", "description": "Results per page 1-5000 (default: 100)"},
                "min_liquidity": {"type": "number", "description": "Minimum liquidity in USD (optional)"},
                "max_liquidity": {"type": "number", "description": "Maximum liquidity in USD (optional)"},
                "min_market_cap": {"type": "number", "description": "Minimum market cap in USD (optional)"},
                "max_market_cap": {"type": "number", "description": "Maximum market cap in USD (optional)"},
                "min_fdv": {"type": "number", "description": "Minimum FDV in USD (optional)"},
                "max_fdv": {"type": "number", "description": "Maximum FDV in USD (optional)"},
                "min_holder": {"type": "integer", "description": "Minimum holder count (optional)"},
                "min_recent_listing_time": {"type": "integer", "description": "Min listing Unix timestamp (optional)"},
                "max_recent_listing_time": {"type": "integer", "description": "Max listing Unix timestamp (optional)"},
                "min_last_trade_unix_time": {"type": "integer", "description": "Min last trade Unix timestamp — filter for actively traded tokens (optional)"},
                "max_last_trade_unix_time": {"type": "integer", "description": "Max last trade Unix timestamp (optional)"},
                "min_volume_24h_usd": {"type": "number", "description": "Minimum 24h volume in USD (optional)"},
                "min_price_change_24h_percent": {"type": "number", "description": "Minimum 24h price change % (e.g. 5.0 for tokens up >5%) (optional)"},
                "min_trade_24h_count": {"type": "integer", "description": "Minimum number of trades in 24h (optional)"},
            },
        },
    },
    {
        "name": "birdeye_tokenlist_v1",
        "description": (
            "Simple sorted token list from Birdeye v1 — quick snapshot of top tokens by volume, market cap, or liquidity. "
            "Lighter than v3 but faster for basic ranking. "
            "sort_by: v24hUSD (24h volume, default) | mc (market cap) | v24hChangePercent (volume change) | liquidity. "
            "sort_type: desc (default) or asc. limit: max 50. "
            "Use for: 'top 20 tokens by volume', 'highest market cap tokens', 'biggest liquidity tokens', "
            "'quick top token list'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sort_by": {"type": "string", "description": "v24hUSD (default), mc, v24hChangePercent, or liquidity"},
                "sort_type": {"type": "string", "description": "desc (default) or asc"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "limit": {"type": "integer", "description": "Results 1-50 (default: 50)"},
                "min_liquidity": {"type": "number", "description": "Minimum liquidity in USD (default: 100)"},
                "max_liquidity": {"type": "number", "description": "Maximum liquidity in USD (optional)"},
            },
        },
    },
    {
        "name": "birdeye_new_listings",
        "description": (
            "Newly listed tokens from Birdeye v2 — the freshest token launches on Solana. "
            "Returns tokens in reverse chronological order (newest first). "
            "meme_platform_enabled=true also includes pump.fun and other meme launchpads. "
            "Use time_to as Unix timestamp to paginate to older listings. "
            "Use for: 'newest token launches', 'latest Solana listings', 'fresh tokens just listed', "
            "'new pump.fun tokens', 'recently created tokens today'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of results 1-20 (default: 20)"},
                "time_to": {"type": "integer", "description": "Unix timestamp — fetch listings before this time for pagination (optional)"},
                "meme_platform_enabled": {"type": "boolean", "description": "Include meme launchpad tokens like pump.fun (Solana only, default: false)"},
            },
        },
    },
    {
        "name": "birdeye_token_markets",
        "description": (
            "All DEX markets/trading pools for a specific token from Birdeye v2. "
            "Returns pool address, DEX name, liquidity, 24h volume, and token pair info for each market. "
            "sort_by: liquidity (default) | volume24h. limit: max 20. "
            "Use for: 'where is BONK trading', 'all pools for this token', 'best liquidity pool for X', "
            "'which DEX has the most volume for WIF', 'find all markets for a token'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address"},
                "sort_by": {"type": "string", "description": "liquidity (default) or volume24h"},
                "sort_type": {"type": "string", "description": "desc (default) or asc"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "limit": {"type": "integer", "description": "Results 1-20 (default: 10)"},
            },
            "required": ["address"],
        },
    },
    # ── Birdeye Transaction Endpoints ─────────────────────────────────────────
    {
        "name": "birdeye_token_txs",
        "description": (
            "Token transactions from Birdeye v3 — the richest transaction endpoint. "
            "Supports filtering by tx type, wallet, DEX/AMM source, pool, and time range. "
            "tx_type: swap | buy | sell | add | remove | all (default: swap). "
            "source: raydium | jupiter | orca | meteora | pump_fun | ... (filter by DEX/AMM). "
            "owner: filter to a specific wallet to see their trades on this token. "
            "pool_id: filter to a specific liquidity pool. "
            "before_time / after_time: Unix timestamp bounds (seconds). limit: 1-100. "
            "Use for: 'recent swaps on BONK', 'buys/sells in last hour', "
            "'what did wallet X trade on this token', 'Raydium trades only', 'transactions in time window'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address"},
                "tx_type": {"type": "string", "description": "swap (default) | buy | sell | add | remove | all"},
                "limit": {"type": "integer", "description": "Results 1-100 (default: 50)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "sort_by": {"type": "string", "description": "block_unix_time (default) | block_number"},
                "sort_type": {"type": "string", "description": "desc (only supported value)"},
                "source": {"type": "string", "description": "DEX/AMM filter: raydium | jupiter | orca | meteora | pump_fun | ... (optional)"},
                "owner": {"type": "string", "description": "Wallet address to filter trades by (optional)"},
                "pool_id": {"type": "string", "description": "Liquidity pool address to filter by (optional)"},
                "before_time": {"type": "integer", "description": "Upper Unix timestamp bound in seconds (optional)"},
                "after_time": {"type": "integer", "description": "Lower Unix timestamp bound in seconds (optional)"},
                "before_block_number": {"type": "integer", "description": "Upper block number bound exclusive (optional)"},
                "after_block_number": {"type": "integer", "description": "Lower block number bound exclusive (optional)"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_txs_all",
        "description": (
            "All Solana DeFi transactions from Birdeye v3 — global feed, not filtered to a specific token. "
            "tx_type: swap | add | remove | all (default: swap). "
            "source: filter by DEX/AMM (raydium, jupiter, orca, meteora, ...). "
            "owner: filter to a wallet's transactions across all tokens. "
            "pool_id: filter to a specific pool. limit: 1-100. "
            "Use for: 'recent DeFi activity on Solana', 'all Raydium swaps right now', "
            "'global swap feed', 'trades by wallet X across all tokens'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tx_type": {"type": "string", "description": "swap (default) | add | remove | all"},
                "limit": {"type": "integer", "description": "Results 1-100 (default: 50)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "sort_by": {"type": "string", "description": "block_unix_time (default) | block_number"},
                "source": {"type": "string", "description": "DEX/AMM filter: raydium | jupiter | orca | meteora | ... (optional)"},
                "owner": {"type": "string", "description": "Wallet address filter (optional)"},
                "pool_id": {"type": "string", "description": "Pool address filter (optional)"},
                "before_time": {"type": "integer", "description": "Upper Unix timestamp bound (optional)"},
                "after_time": {"type": "integer", "description": "Lower Unix timestamp bound (optional)"},
                "before_block_number": {"type": "integer", "description": "Upper block bound (optional)"},
                "after_block_number": {"type": "integer", "description": "Lower block bound (optional)"},
            },
        },
    },
    {
        "name": "birdeye_txs_recent",
        "description": (
            "Most recent DeFi transactions on Solana from Birdeye v3 — high limit (up to 500 per page). "
            "tx_type: swap | add | remove | all (default: swap). "
            "owner: filter to a specific wallet's recent activity. limit: 1-500. "
            "Use for: 'latest swaps on Solana', 'recent DeFi transactions', "
            "'wallet recent activity across all tokens'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tx_type": {"type": "string", "description": "swap (default) | add | remove | all"},
                "limit": {"type": "integer", "description": "Results 1-500 (default: 50)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "owner": {"type": "string", "description": "Wallet address filter (optional)"},
                "before_time": {"type": "integer", "description": "Upper Unix timestamp bound (optional)"},
                "after_time": {"type": "integer", "description": "Lower Unix timestamp bound (optional)"},
                "before_block_number": {"type": "integer", "description": "Upper block bound (optional)"},
                "after_block_number": {"type": "integer", "description": "Lower block bound (optional)"},
            },
        },
    },
    {
        "name": "birdeye_token_txs_v1",
        "description": (
            "Token transactions from Birdeye v1 — simpler and faster for basic use cases. "
            "tx_type: swap | add | remove | all (default: swap). "
            "sort_type: desc (newest first) | asc. limit: 1-50. "
            "Use for: 'latest trades on this token', 'quick transaction list without complex filters'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address"},
                "tx_type": {"type": "string", "description": "swap (default) | add | remove | all"},
                "sort_type": {"type": "string", "description": "desc (default) | asc"},
                "limit": {"type": "integer", "description": "Results 1-50 (default: 50)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_pair_txs",
        "description": (
            "Transactions for a specific DEX pair/pool from Birdeye. "
            "tx_type: swap | add | remove | all (default: swap). "
            "sort_type: desc (default) | asc. limit: 1-50. "
            "Use for: 'all trades in this Raydium pool', 'LP add/remove events for this pair', "
            "'transactions in specific pool address'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "DEX pair/pool contract address"},
                "tx_type": {"type": "string", "description": "swap (default) | add | remove | all"},
                "sort_type": {"type": "string", "description": "desc (default) | asc"},
                "limit": {"type": "integer", "description": "Results 1-50 (default: 50)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_token_txs_by_time",
        "description": (
            "Token transactions filtered by Unix time range from Birdeye — seek by timestamp. "
            "Best for fetching all trades in a specific time window (e.g. last hour, yesterday). "
            "tx_type: swap | add | remove | all (default: swap). "
            "before_time / after_time: Unix timestamp bounds in seconds. limit: 1-100. "
            "Use for: 'all BONK trades in the last hour', 'transactions between 2pm and 4pm UTC', "
            "'trades since timestamp X', 'time-range transaction fetch'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address"},
                "tx_type": {"type": "string", "description": "swap (default) | add | remove | all"},
                "limit": {"type": "integer", "description": "Results 1-100 (default: 50)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "before_time": {"type": "integer", "description": "Upper Unix timestamp bound (seconds, optional)"},
                "after_time": {"type": "integer", "description": "Lower Unix timestamp bound (seconds, optional)"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_pair_txs_by_time",
        "description": (
            "Pair/pool transactions filtered by Unix time range from Birdeye. "
            "tx_type: swap | add | remove | all (default: swap). "
            "before_time / after_time: Unix timestamp bounds (seconds). limit: 1-50. "
            "Use for: 'all trades in this pool in the last 2 hours', "
            "'LP events in time window', 'pair activity between timestamps'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "DEX pair/pool contract address"},
                "tx_type": {"type": "string", "description": "swap (default) | add | remove | all"},
                "limit": {"type": "integer", "description": "Results 1-50 (default: 50)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "before_time": {"type": "integer", "description": "Upper Unix timestamp bound (seconds, optional)"},
                "after_time": {"type": "integer", "description": "Lower Unix timestamp bound (seconds, optional)"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_trader_txs",
        "description": (
            "Transaction history for a specific trader wallet from Birdeye, filtered by time. "
            "Shows the wallet's DeFi activity (swaps, LP adds/removes) across all tokens. "
            "tx_type: swap | add | remove | all (default: swap). "
            "before_time / after_time: Unix timestamp bounds (seconds). limit: 1-100. "
            "Use for: 'what did this wallet trade recently', 'wallet swap history by time', "
            "'analyze trader activity', 'smart money wallet trades', 'track whale transactions'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Trader/wallet address"},
                "tx_type": {"type": "string", "description": "swap (default) | add | remove | all"},
                "limit": {"type": "integer", "description": "Results 1-100 (default: 50)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "before_time": {"type": "integer", "description": "Upper Unix timestamp bound (seconds, optional)"},
                "after_time": {"type": "integer", "description": "Lower Unix timestamp bound (seconds, optional)"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_token_txs_by_volume",
        "description": (
            "Token transactions filtered by trade volume from Birdeye v3 — find large trades and whale activity. "
            "volume_type: usd (filter by USD value, e.g. trades >$10K) | amount (filter by token qty). "
            "min_volume / max_volume: volume filter bounds. "
            "tx_type: swap | buy | sell | add | remove | all (default: swap). "
            "source: filter by DEX/AMM. owner: filter to a wallet. limit: 1-500. Solana only. "
            "Use for: 'whale trades on BONK over $50K', 'large buys in last hour', "
            "'big sells today', 'find significant trades', 'volume-filtered transaction scan'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "token_address": {"type": "string", "description": "Token mint address"},
                "volume_type": {"type": "string", "description": "usd (filter by USD value, default) | amount (token quantity)"},
                "sort_type": {"type": "string", "description": "desc (default) | asc"},
                "tx_type": {"type": "string", "description": "swap (default) | buy | sell | add | remove | all"},
                "min_volume": {"type": "number", "description": "Min trade volume (e.g. 10000 for $10K min USD, optional)"},
                "max_volume": {"type": "number", "description": "Max trade volume (optional)"},
                "sort_by": {"type": "string", "description": "block_unix_time (default) | block_number"},
                "source": {"type": "string", "description": "DEX/AMM filter: raydium | jupiter | orca | pump_fun | ... (optional)"},
                "owner": {"type": "string", "description": "Wallet filter (optional)"},
                "before_time": {"type": "integer", "description": "Upper Unix timestamp bound (optional)"},
                "after_time": {"type": "integer", "description": "Lower Unix timestamp bound (optional)"},
                "limit": {"type": "integer", "description": "Results 1-500 (default: 50)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
            },
            "required": ["token_address"],
        },
    },
    {
        "name": "birdeye_mint_burn_txs",
        "description": (
            "Token mint and burn transactions from Birdeye v3 (Solana only). "
            "Tracks new supply creation (mint) and supply destruction (burn). "
            "type: all | mint | burn (default: all). "
            "sort_type: desc (newest first) | asc. "
            "before_time / after_time: Unix timestamp bounds (seconds). limit: 1-100. "
            "Use for: 'has this token been minting supply', 'token burn history', "
            "'supply inflation check', 'is the team minting new tokens', 'burn events'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address"},
                "type": {"type": "string", "description": "all (default) | mint | burn"},
                "sort_type": {"type": "string", "description": "desc (default) | asc"},
                "before_time": {"type": "integer", "description": "Upper Unix timestamp bound (optional)"},
                "after_time": {"type": "integer", "description": "Lower Unix timestamp bound (optional)"},
                "limit": {"type": "integer", "description": "Results 1-100 (default: 50)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
            },
            "required": ["address"],
        },
    },
    # ── Birdeye Wallet / Trader ───────────────────────────────────────────────
    {
        "name": "birdeye_wallet_current_net_worth",
        "description": (
            "Current portfolio snapshot for a wallet — all token balances with USD values. "
            "filter_value: minimum USD threshold to filter out dust. "
            "flags: pass ['include_low_liquidity'] to include illiquid tokens. "
            "sort_by: value (default) | others. sort_type: desc | asc. limit: 1-100. "
            "chain: solana (default) or any supported chain. "
            "Use for: 'what tokens does wallet X hold', 'portfolio of address', 'wallet balance breakdown'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Wallet address"},
                "filter_value": {"type": "number", "description": "Min USD value threshold (optional)"},
                "sort_by": {"type": "string", "description": "Field to sort by, default: value"},
                "sort_type": {"type": "string", "description": "desc (default) | asc"},
                "limit": {"type": "integer", "description": "Results per page 1-100 (default 20)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "chain": {"type": "string", "description": "Blockchain, default: solana"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "birdeye_wallet_net_worth_history",
        "description": (
            "Historical net worth chart for a wallet — tracks total USD value over time. "
            "type: 1h (hourly) | 1d (daily, default). count: data points to return (1-30). "
            "direction: back (before time) | forward (after time). "
            "Use for: 'how has wallet X grown', 'net worth history', 'portfolio value chart'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Wallet address"},
                "count": {"type": "integer", "description": "Data points 1-30 (default 7)"},
                "direction": {"type": "string", "description": "back (default) | forward"},
                "time": {"type": "string", "description": "Reference timestamp (optional)"},
                "type": {"type": "string", "description": "1h | 1d (default)"},
                "sort_type": {"type": "string", "description": "desc (default) | asc"},
                "chain": {"type": "string", "description": "Blockchain, default: solana"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "birdeye_wallet_net_worth_multi",
        "description": (
            "Net worth summary for up to 100 wallets in one request. "
            "Returns total USD value per wallet. "
            "Use for: 'compare portfolio sizes of these wallets', 'whale wallet net worth comparison'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallets": {"type": "array", "items": {"type": "string"}, "description": "List of wallet addresses (max 100)"},
                "chain": {"type": "string", "description": "Blockchain, default: solana"},
            },
            "required": ["wallets"],
        },
    },
    {
        "name": "birdeye_wallet_net_worth_details",
        "description": (
            "Detailed asset breakdown of a wallet's net worth at a specific time. "
            "Shows individual token positions with their historical USD values. "
            "type: 1h | 1d (default). limit: 1-100. "
            "Use for: 'wallet holdings at specific time', 'historical portfolio snapshot'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Wallet address"},
                "time": {"type": "string", "description": "Snapshot timestamp (optional, defaults to now)"},
                "type": {"type": "string", "description": "1h | 1d (default)"},
                "sort_type": {"type": "string", "description": "desc (default) | asc"},
                "limit": {"type": "integer", "description": "Results per page 1-100 (default 20)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "chain": {"type": "string", "description": "Blockchain, default: solana"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "birdeye_wallet_pnl_summary",
        "description": (
            "Overall PnL summary for a wallet — realized, unrealized, total return, win rate. "
            "duration: all | 90d | 30d | 7d | 24h (default: 7d). "
            "chain: solana | base. "
            "Use for: 'is this wallet profitable', 'wallet PnL summary', 'trader performance overview'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Wallet address"},
                "duration": {"type": "string", "description": "all | 90d | 30d | 7d (default) | 24h"},
                "chain": {"type": "string", "description": "solana (default) | base"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "birdeye_wallet_pnl_details",
        "description": (
            "Per-token PnL breakdown for a wallet — realized + unrealized profit/loss per holding. "
            "Optionally filter to specific token addresses. "
            "duration: all | 90d | 30d | 7d | 24h. sort_by: last_trade | realized_pnl | unrealized_pnl. "
            "chain: solana | base. limit: 1-100. "
            "Use for: 'which tokens made wallet X the most profit', 'trading history PnL per token'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Wallet address"},
                "token_addresses": {"type": "array", "items": {"type": "string"}, "description": "Filter to specific token mints (optional)"},
                "duration": {"type": "string", "description": "all (default) | 90d | 30d | 7d | 24h"},
                "sort_type": {"type": "string", "description": "desc (default) | asc"},
                "sort_by": {"type": "string", "description": "last_trade (default) | realized_pnl | unrealized_pnl"},
                "limit": {"type": "integer", "description": "Results per page 1-100 (default 10)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "chain": {"type": "string", "description": "solana (default) | base"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "birdeye_wallet_pnl_token",
        "description": (
            "PnL for specific tokens in a wallet (deprecated — prefer birdeye_wallet_pnl_details). "
            "token_addresses: comma-separated token mint addresses. "
            "chain: solana | base. "
            "Use for: 'PnL on SOL and BONK for this wallet'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Wallet address"},
                "token_addresses": {"type": "string", "description": "Comma-separated token mint addresses"},
                "chain": {"type": "string", "description": "solana (default) | base"},
            },
            "required": ["wallet", "token_addresses"],
        },
    },
    {
        "name": "birdeye_wallet_pnl_multi",
        "description": (
            "PnL for multiple wallets on a single token — compare trader performance side by side. "
            "wallets: comma-separated wallet addresses. "
            "chain: solana | base. "
            "Use for: 'who made more money on BONK — wallet A or B', 'compare traders on same token'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "token_address": {"type": "string", "description": "Token mint address"},
                "wallets": {"type": "string", "description": "Comma-separated wallet addresses"},
                "chain": {"type": "string", "description": "solana (default) | base"},
            },
            "required": ["token_address", "wallets"],
        },
    },
    {
        "name": "birdeye_wallet_first_funded",
        "description": (
            "First funding transaction for each wallet — shows wallet age and funding origin. "
            "Useful for detecting fresh/coordinated wallets or tracking wallet creation time. "
            "Optionally filter to a specific token's first transfer. "
            "Use for: 'when was this wallet created', 'wallet origin analysis', 'coordinated wallet detection'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallets": {"type": "array", "items": {"type": "string"}, "description": "List of wallet addresses"},
                "token_address": {"type": "string", "description": "Filter to first tx involving this token (optional)"},
                "chain": {"type": "string", "description": "Blockchain, default: solana"},
            },
            "required": ["wallets"],
        },
    },
    {
        "name": "birdeye_token_top_traders",
        "description": (
            "Top traders for a specific token — ranked by volume, trade count, or PnL. "
            "time_frame: 30m | 1h | 2h | 4h | 6h | 8h | 12h | 24h | 2d | 3d | 7d | 14d | 30d | 60d | 90d. "
            "sort_by: volume | trade | total_pnl | unrealized_pnl | realized_pnl | volume_usd. "
            "limit: 1-10. chain: solana, ethereum, bsc, base, arbitrum, etc. "
            "Use for: 'who are the biggest buyers of token X', 'top traders on BONK this week', 'whale traders'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint/contract address"},
                "time_frame": {"type": "string", "description": "30m | 1h | 2h | 4h | 6h | 8h | 12h | 24h (default) | 2d | 3d | 7d | 14d | 30d | 60d | 90d"},
                "sort_type": {"type": "string", "description": "desc (default) | asc"},
                "sort_by": {"type": "string", "description": "volume (default) | trade | total_pnl | unrealized_pnl | realized_pnl | volume_usd"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "limit": {"type": "integer", "description": "Results 1-10 (default 10)"},
                "chain": {"type": "string", "description": "solana (default) | ethereum | bsc | base | arbitrum | etc."},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_trader_gainers_losers",
        "description": (
            "Global top-gaining or top-losing traders across the chain — leaderboard. "
            "type: yesterday | today | 1W (default). sort_type: desc = top gainers, asc = top losers. "
            "chain: solana (default) | ethereum | bsc | base | etc. limit: 1-10. "
            "IMPORTANT: Call this tool ONCE only — do NOT paginate or call multiple times. "
            "Use for: 'who made the most money trading today', 'top DeFi traders this week', 'best traders on Solana'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "yesterday | today | 1W (default)"},
                "sort_type": {"type": "string", "description": "desc (top gainers, default) | asc (top losers)"},
                "limit": {"type": "integer", "description": "Results 1-10 (default 10)"},
                "chain": {"type": "string", "description": "solana (default) | ethereum | bsc | base | etc."},
            },
            "required": [],
        },
    },
    {
        "name": "birdeye_wallet_supported_chains",
        "description": (
            "List all blockchain networks supported by Birdeye wallet APIs. "
            "Use to know which chains can be queried for wallet data, PnL, and net worth. "
            "Use for: 'which chains does Birdeye support', 'is Base chain supported'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "birdeye_wallet_tx_list",
        "description": (
            "Wallet transaction history from Birdeye v1 — swaps, transfers, LP add/remove. "
            "before: tx hash for cursor-based pagination (Solana only). limit: 1-100. "
            "chain: solana (default) or other supported chains. "
            "Use for: 'recent transactions for wallet X', 'swap history', 'what has this wallet been doing'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Wallet address"},
                "limit": {"type": "integer", "description": "Results 1-100 (default 100)"},
                "before": {"type": "string", "description": "Tx hash to paginate from (Solana only, optional)"},
                "ui_amount_mode": {"type": "string", "description": "raw | scaled (default)"},
                "chain": {"type": "string", "description": "Blockchain, default: solana"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "birdeye_wallet_token_list",
        "description": (
            "Wallet token holdings from Birdeye v1 (deprecated — prefer birdeye_wallet_current_net_worth). "
            "Returns all tokens held with balances and USD values. "
            "chain: solana (default) or other chains. "
            "Use for: 'what tokens does wallet X hold', 'wallet portfolio'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Wallet address"},
                "ui_amount_mode": {"type": "string", "description": "raw | scaled (default)"},
                "chain": {"type": "string", "description": "Blockchain, default: solana"},
            },
            "required": ["wallet"],
        },
    },
    # ── Birdeye Misc ──────────────────────────────────────────────────────────
    {
        "name": "birdeye_networks",
        "description": (
            "List all blockchain networks supported by Birdeye. "
            "Use for: 'what chains does Birdeye support', 'supported networks', 'which blockchains are available on Birdeye'."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "birdeye_latest_block",
        "description": (
            "Latest block number on any supported chain from Birdeye. "
            "chain: solana | ethereum | arbitrum | bsc | base | polygon | optimism | avalanche | sui | aptos (default: solana). "
            "Use for: 'what is the current block', 'latest Solana block number', 'latest Ethereum block', 'current block on Base'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "chain": {"type": "string", "description": "Chain name (default: solana)"},
            },
            "required": [],
        },
    },
    {
        "name": "birdeye_token_creation_info",
        "description": (
            "Token creation details — deployer wallet address, creation transaction hash, creation timestamp, initial supply. "
            "chain: solana | bsc | base. "
            "Use for: 'who created this token', 'when was this token deployed', 'deployer wallet', 'token age'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address"},
                "chain": {"type": "string", "description": "solana (default) | bsc | base"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_token_trending",
        "description": (
            "Trending tokens from Birdeye — sorted by rank, volume, or liquidity. "
            "interval: 1h | 4h | 24h. sort_by: rank | volumeUSD | liquidity. limit: 1-20. "
            "Use for: 'trending tokens on Birdeye', 'top tokens by volume today', 'highest liquidity tokens'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sort_by": {"type": "string", "description": "rank (default) | volumeUSD | liquidity"},
                "interval": {"type": "string", "description": "1h | 4h | 24h (default)"},
                "sort_type": {"type": "string", "description": "asc (default for rank) | desc"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "limit": {"type": "integer", "description": "Results 1-20 (default 20)"},
                "chain": {"type": "string", "description": "solana (default) or other chain"},
            },
            "required": [],
        },
    },
    {
        "name": "birdeye_meme_token_detail",
        "description": (
            "Detailed meme token data from Birdeye v3 — bonding curve progress %, graduation status, "
            "platform (pump.fun/moonshot/raydium launchlab), creator, initial liquidity. "
            "chain: solana | bsc | monad. "
            "Use for: 'pump.fun token progress', 'is this token graduated', 'bonding curve status', 'meme token detail'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Meme token mint address"},
                "chain": {"type": "string", "description": "solana (default) | bsc | monad"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_meme_token_list",
        "description": (
            "Meme token list from Birdeye v3 — screen and filter meme tokens by platform, graduation, progress, liquidity, market cap. "
            "sort_by: progress_percent | graduated_time | creation_time | liquidity | market_cap | fdv | recent_listing_time | holder | volume_* | price_change_* | trade_*_count. "
            "source: all | pump_dot_fun | moonshot | raydium_launchlab | meteora_dynamic_bonding_curve | others. "
            "graduated: true (only graduated tokens) | false (only bonding curve tokens). "
            "Use for: 'top pump.fun tokens by progress', 'nearly graduated meme coins', 'new meme launches', 'meme screener'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sort_by": {"type": "string", "description": "creation_time (default) | progress_percent | graduated_time | liquidity | market_cap | fdv | holder | volume_* | price_change_* | trade_*_count"},
                "sort_type": {"type": "string", "description": "desc (default) | asc"},
                "source": {"type": "string", "description": "all | pump_dot_fun | moonshot | raydium_launchlab | meteora_dynamic_bonding_curve | others (optional)"},
                "creator": {"type": "string", "description": "Filter by creator wallet (optional)"},
                "graduated": {"type": "boolean", "description": "true = graduated only | false = bonding curve only (optional)"},
                "min_progress_percent": {"type": "number", "description": "Min bonding curve progress 0-100 (optional)"},
                "max_progress_percent": {"type": "number", "description": "Max bonding curve progress 0-100 (optional)"},
                "min_liquidity": {"type": "number", "description": "Min liquidity USD (optional)"},
                "max_liquidity": {"type": "number", "description": "Max liquidity USD (optional)"},
                "min_market_cap": {"type": "number", "description": "Min market cap USD (optional)"},
                "max_market_cap": {"type": "number", "description": "Max market cap USD (optional)"},
                "min_fdv": {"type": "number", "description": "Min FDV USD (optional)"},
                "max_fdv": {"type": "number", "description": "Max FDV USD (optional)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "limit": {"type": "integer", "description": "Results 1-100 (default 100)"},
                "chain": {"type": "string", "description": "solana (default) | bsc | monad"},
            },
            "required": [],
        },
    },
    {
        "name": "birdeye_token_security",
        "description": (
            "Token security audit from Birdeye — mint authority status, freeze authority, top holder concentration, "
            "LP lock status, creator holdings, honeypot detection. "
            "chain: solana | ethereum | bsc | base | arbitrum | etc. "
            "Use for: 'is this token safe', 'rug pull check', 'mint authority revoked', 'security audit', 'freeze authority'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint/contract address"},
                "chain": {"type": "string", "description": "solana (default) | ethereum | bsc | base | arbitrum | etc."},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_smart_money_tokens",
        "description": (
            "Tokens being accumulated by smart money / institutional wallets on Solana. "
            "interval: 1d | 7d | 30d. "
            "trader_style: all | risk_averse (conservative traders) | risk_balancers (balanced) | trenchers (momentum). "
            "sort_by: smart_traders_no (most smart money wallets) | net_flow (net USD inflow) | market_cap. "
            "Use for: 'what are smart money wallets buying', 'institutional accumulation', 'smart money signals', 'follow smart traders'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "interval": {"type": "string", "description": "1d (default) | 7d | 30d"},
                "trader_style": {"type": "string", "description": "all (default) | risk_averse | risk_balancers | trenchers"},
                "sort_by": {"type": "string", "description": "smart_traders_no (default) | net_flow | market_cap"},
                "sort_type": {"type": "string", "description": "desc (default) | asc"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "limit": {"type": "integer", "description": "Results 1-20 (default 20)"},
            },
            "required": [],
        },
    },
    {
        "name": "birdeye_all_time_trades_single",
        "description": (
            "Cumulative all-time trade statistics for a single token over a time frame — total buy/sell count, volume, unique wallets. "
            "time_frame: 1m | 5m | 30m | 1h | 2h | 4h | 8h | 24h | 3d | 7d | 14d | 30d | 90d | 180d | 1y | alltime. "
            "Use for: 'total trades ever on token X', 'cumulative trading stats', 'all-time buy/sell volume'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address"},
                "time_frame": {"type": "string", "description": "1m | 5m | 30m | 1h | 2h | 4h | 8h | 24h (default) | 3d | 7d | 14d | 30d | 90d | 180d | 1y | alltime"},
                "ui_amount_mode": {"type": "string", "description": "scaled (default) | raw | both"},
                "chain": {"type": "string", "description": "solana (default) or other chain"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_all_time_trades_multi",
        "description": (
            "Cumulative all-time trade statistics for multiple tokens at once. "
            "list_address: comma-separated token addresses. "
            "time_frame: 1m | 5m | 30m | 1h | 2h | 4h | 8h | 24h | 3d | 7d | 14d | 30d | 90d | 180d | 1y | alltime. "
            "Use for: 'compare all-time trade counts of these tokens', 'batch cumulative trade stats'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "list_address": {"type": "string", "description": "Comma-separated token mint addresses"},
                "time_frame": {"type": "string", "description": "1m | 5m | 30m | 1h | 2h | 4h | 8h | 24h (default) | 3d | 7d | 14d | 30d | 90d | 180d | 1y | alltime"},
                "ui_amount_mode": {"type": "string", "description": "scaled (default) | raw | both"},
                "chain": {"type": "string", "description": "solana (default) or other chain"},
            },
            "required": ["list_address"],
        },
    },
    {
        "name": "birdeye_search",
        "description": (
            "Search tokens and markets on Birdeye by name, symbol, or address across all chains. "
            "target: all | token | market. search_mode: exact | fuzzy. search_by: combination | address | name | symbol. "
            "sort_by: fdv | marketcap | liquidity | price | price_change_24h_percent | trade_24h | volume_24h_usd. "
            "markets: comma-separated — Raydium, Meteora, Pump.fun, Orca (Solana only). "
            "verify_token: true = only verified tokens (Solana only). "
            "Use for: 'find token called X', 'search for BONK', 'find all pools for symbol', 'cross-chain token search'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "Search term (token name, symbol, or address)"},
                "chain": {"type": "string", "description": "solana (default) | ethereum | bsc | base | all | etc."},
                "target": {"type": "string", "description": "all (default) | token | market"},
                "search_mode": {"type": "string", "description": "fuzzy (default) | exact"},
                "search_by": {"type": "string", "description": "symbol (default) | combination | address | name"},
                "sort_by": {"type": "string", "description": "liquidity (default) | fdv | marketcap | price | price_change_24h_percent | trade_24h | volume_24h_usd"},
                "sort_type": {"type": "string", "description": "desc (default) | asc"},
                "verify_token": {"type": "boolean", "description": "true = only verified tokens, Solana only (optional)"},
                "markets": {"type": "string", "description": "Comma-separated market filter: Raydium, Meteora, Pump.fun, Orca (Solana only, optional)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "limit": {"type": "integer", "description": "Results 1-20 (default 20)"},
            },
            "required": [],
        },
    },
    {
        "name": "birdeye_credits",
        "description": (
            "API credit usage for the current Birdeye API key — check remaining quota and historical consumption. "
            "time_from / time_to: Unix timestamp range (optional). "
            "Use for: 'how many Birdeye API credits do I have left', 'API usage check', 'credit consumption'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "time_from": {"type": "integer", "description": "Start Unix timestamp (optional)"},
                "time_to": {"type": "integer", "description": "End Unix timestamp (optional)"},
            },
            "required": [],
        },
    },
    # ── Birdeye Balance / Transfer ────────────────────────────────────────────
    {
        "name": "birdeye_wallet_balance_change",
        "description": (
            "Wallet balance change history — inflows and outflows per token over time. "
            "type: SOL | SPL (filter by token type). change_type: increase | decrease. "
            "Filter by token_address, time range, pagination. limit: 1-100. "
            "Use for: 'when did wallet X receive SOL', 'inflow/outflow history', "
            "'did this wallet deposit recently', 'balance change timeline'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Wallet address"},
                "token_address": {"type": "string", "description": "Filter to specific token (optional)"},
                "time_from": {"type": "integer", "description": "Start Unix timestamp (optional)"},
                "time_to": {"type": "integer", "description": "End Unix timestamp (optional)"},
                "type": {"type": "string", "description": "SOL | SPL (optional filter)"},
                "change_type": {"type": "string", "description": "increase | decrease (optional)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "limit": {"type": "integer", "description": "Results 1-100 (default 20)"},
                "chain": {"type": "string", "description": "Blockchain, default: solana"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_wallet_token_balance",
        "description": (
            "Current balance of specific tokens in a wallet — batch lookup for multiple tokens at once. "
            "Solana only. Returns balance and USD value per token. "
            "Use for: 'how much BONK and SOL does wallet X hold', 'check balances of these tokens in a wallet'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Wallet address"},
                "token_addresses": {"type": "array", "items": {"type": "string"}, "description": "List of token mint addresses to check"},
                "chain": {"type": "string", "description": "Blockchain, default: solana"},
            },
            "required": ["wallet", "token_addresses"],
        },
    },
    {
        "name": "birdeye_token_transfers",
        "description": (
            "All on-chain transfers of a token with rich filtering — amount, value, sender, receiver, time. "
            "cursor: use from previous response for pagination. "
            "from_value / to_value: USD value range filter. from_amount / to_amount: token amount range. "
            "Use for: 'large transfers of BONK today', 'who sent tokens to wallet X', "
            "'token flow from a specific wallet', 'transfer history for token'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "token_address": {"type": "string", "description": "Token mint address"},
                "time_from": {"type": "number", "description": "Start Unix timestamp (optional)"},
                "time_to": {"type": "number", "description": "End Unix timestamp (optional)"},
                "from_amount": {"type": "number", "description": "Min token amount filter (optional)"},
                "to_amount": {"type": "number", "description": "Max token amount filter (optional)"},
                "from_value": {"type": "number", "description": "Min USD value filter (optional)"},
                "to_value": {"type": "number", "description": "Max USD value filter (optional)"},
                "from_wallet": {"type": "string", "description": "Filter by sender wallet (optional)"},
                "to_wallet": {"type": "string", "description": "Filter by receiver wallet (optional)"},
                "cursor": {"type": "string", "description": "Pagination cursor from previous response (optional)"},
                "limit": {"type": "number", "description": "Results per page (optional)"},
            },
            "required": ["token_address"],
        },
    },
    {
        "name": "birdeye_token_transfer_total",
        "description": (
            "Total count of token transfers matching filters — aggregate number without full listing. "
            "Same filters as birdeye_token_transfers. "
            "Use for: 'how many transfers has this token had today', 'transfer volume count', "
            "'how active is this token in terms of on-chain movement'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "token_address": {"type": "string", "description": "Token mint address"},
                "time_from": {"type": "number", "description": "Start Unix timestamp (optional)"},
                "time_to": {"type": "number", "description": "End Unix timestamp (optional)"},
                "from_amount": {"type": "number", "description": "Min token amount filter (optional)"},
                "to_amount": {"type": "number", "description": "Max token amount filter (optional)"},
                "from_value": {"type": "number", "description": "Min USD value filter (optional)"},
                "to_value": {"type": "number", "description": "Max USD value filter (optional)"},
                "from_wallet": {"type": "string", "description": "Filter by sender wallet (optional)"},
                "to_wallet": {"type": "string", "description": "Filter by receiver wallet (optional)"},
            },
            "required": ["token_address"],
        },
    },
    {
        "name": "birdeye_wallet_transfers",
        "description": (
            "Transfer history for a wallet — all token movements in and out. "
            "flow: in | out (optional direction filter). "
            "Filter by token, time, amount, value, counterparty wallet. cursor for pagination. "
            "Use for: 'all transfers in/out of wallet X', 'when did wallet X send tokens', "
            "'track money movements of a wallet', 'wallet inflow/outflow analysis'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Wallet address"},
                "token_address": {"type": "string", "description": "Filter to specific token (optional)"},
                "flow": {"type": "string", "description": "in | out (optional direction filter)"},
                "time_from": {"type": "number", "description": "Start Unix timestamp (optional)"},
                "time_to": {"type": "number", "description": "End Unix timestamp (optional)"},
                "from_amount": {"type": "number", "description": "Min amount filter (optional)"},
                "to_amount": {"type": "number", "description": "Max amount filter (optional)"},
                "from_value": {"type": "number", "description": "Min USD value filter (optional)"},
                "to_value": {"type": "number", "description": "Max USD value filter (optional)"},
                "from_wallet": {"type": "string", "description": "Filter by sender wallet (optional)"},
                "to_wallet": {"type": "string", "description": "Filter by receiver wallet (optional)"},
                "cursor": {"type": "string", "description": "Pagination cursor (optional)"},
                "limit": {"type": "number", "description": "Results per page (optional)"},
                "chain": {"type": "string", "description": "Blockchain, default: solana"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "birdeye_wallet_transfer_total",
        "description": (
            "Total count of wallet transfers matching filters — aggregate without full listing. "
            "Same filters as birdeye_wallet_transfers. "
            "Use for: 'how many transfers has wallet X made', 'total outflows count', "
            "'transfer activity level for a wallet'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Wallet address"},
                "token_address": {"type": "string", "description": "Filter to specific token (optional)"},
                "flow": {"type": "string", "description": "in | out (optional)"},
                "time_from": {"type": "number", "description": "Start Unix timestamp (optional)"},
                "time_to": {"type": "number", "description": "End Unix timestamp (optional)"},
                "from_amount": {"type": "number", "description": "Min amount filter (optional)"},
                "to_amount": {"type": "number", "description": "Max amount filter (optional)"},
                "from_value": {"type": "number", "description": "Min USD value filter (optional)"},
                "to_value": {"type": "number", "description": "Max USD value filter (optional)"},
                "from_wallet": {"type": "string", "description": "Filter by sender wallet (optional)"},
                "to_wallet": {"type": "string", "description": "Filter by receiver wallet (optional)"},
                "chain": {"type": "string", "description": "Blockchain, default: solana"},
            },
            "required": ["wallet"],
        },
    },
    {
        "name": "birdeye_wallet_single_token_balance",
        "description": (
            "Current balance of one specific token in a wallet (v1 beta). "
            "Returns balance and USD value for a single wallet+token pair. "
            "Use for: 'how much SOL does wallet X have', 'does wallet X hold any BONK', "
            "'quick balance check for one token'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Wallet address"},
                "token_address": {"type": "string", "description": "Token mint address"},
                "ui_amount_mode": {"type": "string", "description": "scaled (default) | raw"},
                "chain": {"type": "string", "description": "Blockchain, default: solana"},
            },
            "required": ["wallet", "token_address"],
        },
    },
    # ── Birdeye Holder ────────────────────────────────────────────────────────
    {
        "name": "birdeye_token_holders",
        "description": (
            "Top token holders list from Birdeye v3 — wallet address, token balance, % of total supply. "
            "limit: 1-100 (default 100). offset+limit ≤ 10000. "
            "Use for: 'top holders of token X', 'who holds the most BONK', "
            "'holder concentration check', 'is supply concentrated in few wallets'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token mint address"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "limit": {"type": "integer", "description": "Results 1-100 (default 100)"},
                "ui_amount_mode": {"type": "string", "description": "scaled (default) | raw"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "birdeye_holder_batch",
        "description": (
            "Check token holdings for a batch of wallets — how much of a token each wallet holds. "
            "Useful for checking if a watchlist of wallets holds a specific token. "
            "Use for: 'do these wallets hold TOKEN X', 'batch holder check', 'how much does each of these wallets hold'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wallets": {"type": "array", "items": {"type": "string"}, "description": "List of wallet addresses"},
                "token_address": {"type": "string", "description": "Token mint address"},
                "ui_amount_mode": {"type": "string", "description": "scaled (default) | raw"},
            },
            "required": ["wallets", "token_address"],
        },
    },
    {
        "name": "birdeye_holder_distribution",
        "description": (
            "Token holder distribution — top-N holders or filter holders by supply % range. "
            "mode: top (return top N holders) | percent (filter by min_percent/max_percent of supply). "
            "address_type: wallet | token_account. top_n: 1-20 (default 10). "
            "IMPORTANT: Call this tool ONCE only — do NOT paginate or call multiple times. "
            "Use for: 'holder concentration analysis', 'how decentralized is this token', "
            "'top 10 holders % of supply', 'wallets holding between 1% and 5% of supply'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "token_address": {"type": "string", "description": "Token mint address"},
                "address_type": {"type": "string", "description": "wallet (default) | token_account"},
                "mode": {"type": "string", "description": "top (default) | percent"},
                "top_n": {"type": "integer", "description": "Number of top holders (mode=top, 1-20, default 10)"},
                "min_percent": {"type": "number", "description": "Min supply % threshold (mode=percent, optional)"},
                "max_percent": {"type": "number", "description": "Max supply % threshold (mode=percent, optional)"},
            },
            "required": ["token_address"],
        },
    },
    {
        "name": "birdeye_holder_profile",
        "description": (
            "Token holder profile summary — total holder count, new holders, churned holders, volume breakdown. "
            "interval: 1h (default). include_zero_balance: include wallets with 0 balance. "
            "Use for: 'holder growth rate', 'how many new holders joined', 'holder retention', "
            "'is the token gaining or losing holders'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "token_address": {"type": "string", "description": "Token mint address"},
                "interval": {"type": "string", "description": "Time interval, default: 1h"},
                "ui_amount_mode": {"type": "string", "description": "scaled | raw (default)"},
                "include_zero_balance": {"type": "boolean", "description": "Include zero-balance holders (default true)"},
            },
            "required": ["token_address"],
        },
    },
    {
        "name": "birdeye_holder_positions",
        "description": (
            "Token holder positions with behavioral labels — filter by bundler, sniper, insider, or dev tags. "
            "labels: comma-separated filter — bundler | sniper | insider | dev. "
            "sort_by: amount (default). order_type: desc | asc. limit: 1-50. "
            "Use for: 'are there snipers holding this token', 'insider wallets', "
            "'bundler concentration check', 'dev wallet still holding', 'suspicious holder analysis'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "token_address": {"type": "string", "description": "Token mint address"},
                "labels": {"type": "string", "description": "Comma-separated label filter: bundler | sniper | insider | dev (optional)"},
                "sort_by": {"type": "string", "description": "amount (default)"},
                "order_type": {"type": "string", "description": "desc (default) | asc"},
                "ui_amount_mode": {"type": "string", "description": "scaled | raw (default)"},
                "include_zero_balance": {"type": "boolean", "description": "Include zero-balance holders (default true)"},
                "offset": {"type": "integer", "description": "Pagination offset (optional)"},
                "limit": {"type": "integer", "description": "Results 1-50 (default 50)"},
            },
            "required": ["token_address"],
        },
    },
    # ── Tensor Trade ─────────────────────────────────────────────────────────
    {
        "name": "tensor_mint_proof",
        "description": (
            "Fetch the Merkle proof for a compressed NFT (cNFT) on Tensor. "
            "Required when building cNFT transactions (buy, list, transfer). "
            "Use for: 'get cNFT proof', 'compressed NFT merkle proof', 'tensor_mint_proof'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mint": {"type": "string", "description": "Mint address of the compressed NFT"},
            },
            "required": ["mint"],
        },
    },
    {
        "name": "tensor_mint_collection",
        "description": (
            "Browse all NFTs in a Tensor collection with flexible sorting. "
            "Returns mint addresses, listing prices, rarity ranks, last sale data. "
            "sort_by options: ListingPriceAsc/Desc, RarityAsc/Desc, LastSaleAsc/Desc, NameAsc/Desc, MintAsc/Desc, etc. "
            "Use for: 'show NFTs in collection', 'cheapest listed NFTs', 'rarest NFTs in Mad Lads', 'browse collection'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "coll_id": {"type": "string", "description": "Collection ID (collId)"},
                "sort_by": {"type": "string", "description": "Sort order — e.g. ListingPriceAsc, RarityAsc, LastSaleDesc"},
                "limit": {"type": "integer", "description": "Number of results (1-250)"},
                "cursor": {"type": "string", "description": "Pagination cursor (optional)"},
                "filters": {"type": "string", "description": "Encoded trait filters (optional)"},
            },
            "required": ["coll_id", "sort_by", "limit"],
        },
    },
    {
        "name": "tensor_mint_active_listings",
        "description": (
            "Fetch active (live) listings for an NFT collection on Tensor. "
            "Returns listed price, normalized price, listing time, seller, rarity rank. "
            "sort_by options: ListingPriceAsc/Desc, ListingNormalizedPriceAsc/Desc, ListedTimeDesc, RankHrttAsc/Desc, etc. "
            "Use for: 'what's listed on Tensor', 'floor listings for DeGods', 'active sales in collection'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "coll_id": {"type": "string", "description": "Collection ID (collId)"},
                "sort_by": {"type": "string", "description": "Sort order — e.g. ListingPriceAsc, ListedTimeDesc"},
                "limit": {"type": "integer", "description": "Number of results (1-250)"},
                "cursor": {"type": "string", "description": "Pagination cursor (optional)"},
                "include_private_taker": {"type": "boolean", "description": "Include private-taker listings (optional)"},
                "filters": {"type": "string", "description": "Encoded trait filters (optional)"},
            },
            "required": ["coll_id", "sort_by", "limit"],
        },
    },
    {
        "name": "tensor_mint_activity",
        "description": (
            "Transaction activity history for a specific NFT mint on Tensor. "
            "Returns sales, listings, de-listings, bids, transfers with timestamps and prices. "
            "tx_types: comma-separated — SALE_BUY_NOW, LIST, DELIST, MAKE_BID, CANCEL_BID, TRANSFER, etc. "
            "Use for: 'NFT sale history', 'when was this NFT last sold', 'activity for mint address'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mint": {"type": "string", "description": "NFT mint address"},
                "limit": {"type": "integer", "description": "Number of results (1-100)"},
                "tx_types": {"type": "string", "description": "Comma-separated transaction type filter (optional)"},
                "cursor": {"type": "string", "description": "Pagination cursor (optional)"},
            },
            "required": ["mint", "limit"],
        },
    },
    {
        "name": "tensor_collections_browse",
        "description": (
            "Browse/rank all NFT collections on Tensor by volume, listings, bids, or name. "
            "sort_by options: statsV2.volume1h/24h/7d/All:desc/asc, statsV2.numBids:desc/asc, "
            "statsV2.numListed:desc/asc, createdAt:asc/desc, slugDisplay:asc/desc. "
            "Use for: 'top NFT collections', 'trending collections by volume', 'most listed collections'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sort_by": {"type": "string", "description": "Sort field and direction — e.g. statsV2.volume24h:desc"},
                "limit": {"type": "integer", "description": "Number of results (1-100)"},
                "slug_displays": {"type": "string", "description": "Comma-separated slug filters (optional)"},
                "coll_ids": {"type": "string", "description": "Comma-separated collection ID filters (optional)"},
                "vocs": {"type": "string", "description": "Verified on-chain collections filter (optional, max 10 combined with fvcs)"},
                "fvcs": {"type": "string", "description": "Fungible verified collections filter (optional, max 10 combined with vocs)"},
                "page": {"type": "integer", "description": "Page number (≥1, optional)"},
            },
            "required": ["sort_by", "limit"],
        },
    },
    {
        "name": "tensor_find_collection",
        "description": (
            "Look up a Tensor NFT collection by collId, slug, or slugDisplay. "
            "Returns full collection metadata: floor price, volume, supply, royalties, verified status. "
            "Use for: 'find Mad Lads collection', 'collection info for DeGods', 'lookup collection by slug'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Collection identifier: collId, slug, or slugDisplay"},
            },
            "required": ["filter"],
        },
    },
    {
        "name": "tensor_search_collections",
        "description": (
            "Search Tensor NFT collections by name or keyword. "
            "Returns matching collections with floor price, volume, verified status. "
            "Use for: 'search NFT collections', 'find collections named pixel', 'tensor collection search'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (optional)"},
                "sort_by": {"type": "string", "description": "Sort order (optional)"},
                "limit": {"type": "integer", "description": "Results 1-20 (optional)"},
                "page": {"type": "integer", "description": "Page number ≥1 (optional)"},
            },
            "required": [],
        },
    },
    {
        "name": "tensor_collections_mints",
        "description": (
            "Fetch collection and metadata for a list of NFT mint addresses on Tensor. "
            "Use for: 'which collection does this NFT belong to', 'batch NFT collection lookup'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mints": {"type": "string", "description": "Comma-separated NFT mint addresses"},
            },
            "required": ["mints"],
        },
    },
    {
        "name": "tensor_collections_list",
        "description": (
            "Paginated list of all mint addresses in a Tensor collection. "
            "Use for: 'get all NFTs in collection', 'list every mint in Mad Lads'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "coll_id": {"type": "string", "description": "Collection ID"},
                "limit": {"type": "integer", "description": "Results per page (1-1000)"},
                "after": {"type": "string", "description": "Cursor for next page (optional)"},
            },
            "required": ["coll_id", "limit"],
        },
    },
    {
        "name": "tensor_collections_traits",
        "description": (
            "All trait types and values with rarity counts for a Tensor NFT collection. "
            "Use for: 'what traits does this collection have', 'trait rarity for Mad Lads', 'list all attributes'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "coll_id": {"type": "string", "description": "Collection ID"},
            },
            "required": ["coll_id"],
        },
    },
    {
        "name": "tensor_collections_coll_bids",
        "description": (
            "Active collection-level bids (offers to buy any NFT in a collection) on Tensor. "
            "Returns bid price, bidder, quantity, expiry. "
            "Use for: 'collection offers on Mad Lads', 'what are people bidding on this collection', 'best bid price'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "coll_id": {"type": "string", "description": "Collection ID"},
                "limit": {"type": "integer", "description": "Results (1-100)"},
                "cursor": {"type": "string", "description": "Pagination cursor (optional)"},
            },
            "required": ["coll_id", "limit"],
        },
    },
    {
        "name": "tensor_collections_nft_bids",
        "description": (
            "Active NFT-specific bids for given mint addresses on Tensor. "
            "Use for: 'bids on this NFT', 'who is offering to buy this mint', 'NFT-level offers'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mints": {"type": "string", "description": "Comma-separated NFT mint addresses"},
            },
            "required": ["mints"],
        },
    },
    {
        "name": "tensor_collections_trait_bids",
        "description": (
            "Active trait-based bids for a collection on Tensor (bids targeting specific trait values). "
            "Use for: 'bids on Gold trait', 'trait offers for collection', 'trait-level bid book'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "coll_id": {"type": "string", "description": "Collection ID"},
                "limit": {"type": "integer", "description": "Results (1-100)"},
                "cursor": {"type": "string", "description": "Pagination cursor (optional)"},
            },
            "required": ["coll_id", "limit"],
        },
    },
    {
        "name": "tensor_collections_pools",
        "description": (
            "TSWAP AMM liquidity pools for an NFT collection on Tensor. "
            "Returns pool type, price, spot price, fee, delta, NFT count, SOL balance. "
            "Use for: 'AMM pools for collection', 'Tensor pool liquidity', 'TSWAP pools'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "coll_id": {"type": "string", "description": "Collection ID"},
            },
            "required": ["coll_id"],
        },
    },
    {
        "name": "tensor_collections_tamm_pools",
        "description": (
            "TAMM AMM pools for an NFT collection on Tensor. "
            "Use for: 'TAMM pools for collection', 'Tensor AMM liquidity'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "coll_id": {"type": "string", "description": "Collection ID"},
            },
            "required": ["coll_id"],
        },
    },
    {
        "name": "tensor_collections_tx_history",
        "description": (
            "Transaction history across Tensor collections — sales, listings, bids, transfers. "
            "limit is required; collId, wallet, tx_types, sources are optional filters. "
            "tx_types: comma-separated — SALE_BUY_NOW, LIST, DELIST, MAKE_BID, CANCEL_BID, etc. "
            "Use for: 'recent Tensor sales', 'transaction history for collection', 'sales for wallet on Tensor'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of results (1-100)"},
                "coll_id": {"type": "string", "description": "Filter by collection ID (optional)"},
                "wallet": {"type": "string", "description": "Filter by wallet address (optional)"},
                "tx_types": {"type": "string", "description": "Comma-separated transaction type filter (optional)"},
                "sources": {"type": "string", "description": "Comma-separated marketplace source filter (optional)"},
            },
            "required": ["limit"],
        },
    },
    {
        "name": "tensor_whitelist_info",
        "description": (
            "Get whitelist info for Tensor collections by collection ID or whitelist address. "
            "Use for: 'whitelist details for collection', 'is this collection whitelisted on Tensor', 'whitelist address lookup'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "coll_ids": {"type": "string", "description": "Comma-separated collection IDs (optional)"},
                "whitelists": {"type": "string", "description": "Comma-separated whitelist addresses (optional)"},
            },
            "required": [],
        },
    },
    {
        "name": "tensor_trait_bid_attributes",
        "description": (
            "Get trait bid attributes for a given Tensor bid state address. "
            "Use for: 'what traits is this bid targeting', 'trait bid details', 'bid state attributes'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "bid_state_address": {"type": "string", "description": "The bid state address"},
            },
            "required": ["bid_state_address"],
        },
    },
    {
        "name": "tensor_refresh_metadata",
        "description": (
            "Trigger a metadata refresh for a specific NFT on Tensor. "
            "Subject to a 1-hour cooldown per mint address. "
            "Use for: 'refresh NFT metadata', 'update NFT image on Tensor', 'force metadata sync'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mint": {"type": "string", "description": "NFT mint address to refresh"},
            },
            "required": ["mint"],
        },
    },
    {
        "name": "tensor_refresh_rarities",
        "description": (
            "Trigger a rarity score refresh for an entire NFT collection on Tensor. "
            "Subject to a 1-hour cooldown per collection. "
            "Use for: 'refresh collection rarities', 'update rarity scores', 'recalculate rarity'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "coll_id": {"type": "string", "description": "Collection ID to refresh rarities for"},
            },
            "required": ["coll_id"],
        },
    },
    {
        "name": "tensor_edit_bid",
        "description": (
            "Build a Tensor bid-edit transaction payload — fetches serialized transaction data for updating price, quantity, or expiry on an existing Tensor NFT/cNFT bid. "
            "Does NOT execute or submit anything — returns raw transaction bytes for the caller to sign. "
            "No wallet auth required — this is purely a data retrieval call. "
            "bidStateAddress and blockhash are required. "
            "Use for: 'change my Tensor bid price', 'update bid quantity', 'extend bid expiry', "
            "'edit NFT offer on Tensor', 'modify my Tensor collection bid', 'tensor_edit_bid'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "bid_state_address": {"type": "string", "description": "The bid state address to edit"},
                "blockhash": {"type": "string", "description": "Recent Solana blockhash for the transaction"},
                "price": {"type": "number", "description": "New bid price (≥ 0, optional)"},
                "quantity": {"type": "integer", "description": "New quantity of NFTs to bid on (≥ 1, optional)"},
                "expire_in": {"type": "integer", "description": "Seconds until bid expires (≥ 0, optional)"},
                "maker_broker": {"type": "string", "description": "Maker broker address for fee rebates (optional)"},
                "private_taker": {"type": "string", "description": "Private taker address (optional)"},
                "use_shared_escrow": {"type": "boolean", "description": "Use shared escrow (optional)"},
                "compute": {"type": "integer", "description": "Compute units for the transaction (optional)"},
                "priority_micro_lamports": {"type": "integer", "description": "Priority fee in micro-lamports (optional)"},
            },
            "required": ["bid_state_address", "blockhash"],
        },
    },
]


# ─── Tools requiring authentication ───────────────────────────────────────────

_REQUIRES_JWT = {
    "jito_tip_floor",
    "token_security",
    "wallet_balance", "user_transactions", "limit_orders", "dca_orders",
}


# ─── Dispatcher ───────────────────────────────────────────────────────────────

async def dispatch(tool_name: str, tool_input: dict, jwt_token: str = None) -> Any:
    if tool_name in _REQUIRES_JWT and (not jwt_token or jwt_token == "None"):
        return {"error_type": "auth_required", "message": f"'{tool_name}' requires authentication — please connect your wallet first."}

    try:
        result = await _dispatch_inner(tool_name, tool_input, jwt_token)
        return _cap(result)
    except httpx.HTTPStatusError as e:
        sc = e.response.status_code
        body = e.response.text[:200]
        if sc == 400:
            return {"error_type": "user_error", "message": "The request parameters are invalid.", "detail": body}
        if sc == 404:
            return {"error_type": "not_found", "message": "The requested resource was not found — the token address, wallet, or market may not exist.", "detail": body}
        if sc == 422:
            return {"error_type": "user_error", "message": "The input format is invalid — check the address or parameter format.", "detail": body}
        if sc == 429:
            return {"error_type": "rate_limited", "message": "The external API rate limit was reached — please try again in a few seconds."}
        if sc in (401, 403):
            return {"error_type": "config_error", "message": "API key missing or unauthorised for this service."}
        return {"error_type": "server_error", "message": "The external API returned an error on its end.", "detail": body}
    except httpx.TimeoutException:
        return {"error_type": "timeout", "message": "The external API did not respond in time — the service may be temporarily slow or overloaded."}
    except httpx.RequestError as e:
        return {"error_type": "network_error", "message": "Could not reach the external API — the service may be down or unreachable."}
    except Exception as e:
        return {"error_type": "internal_error", "message": str(e)}


async def _dispatch_inner(tool_name: str, tool_input: dict, jwt_token: str) -> Any:
    match tool_name:
            # Jupiter
            case "jup_prices":
                return await jup_prices(**tool_input)
            case "jup_search":
                return await jup_search(**tool_input)
            case "jup_recent":
                return await jup_recent(**tool_input)
            case "jup_trending":
                return await jup_trending(**tool_input)
            case "jup_tokens_tag":
                return await jup_tokens_tag(**tool_input)
            case "jup_verify_eligibility":
                return await jup_verify_eligibility(**tool_input)
            case "jup_quote":
                return await jup_quote(**tool_input)
            # Meteora
            case "meteora_pools":
                return await meteora_pools(**tool_input)
            # DexScreener
            case "dex_trending":
                return await dex_trending()
            case "dex_search":
                return await dex_search(**tool_input)
            case "dex_token":
                return await dex_token(**tool_input)
            case "dex_latest_pairs":
                return await dex_latest_pairs()
            # Raydium
            case "raydium_prices":
                return await raydium_prices()
            case "raydium_clmm_pools":
                return await raydium_clmm_pools(**tool_input)
            case "raydium_swap_quote":
                return await raydium_swap_quote(**tool_input)
            case "raydium_pools":
                return await raydium_pools(**tool_input)
            case "raydium_pool_search":
                return await raydium_pool_search(**tool_input)
            case "raydium_pool_by_lp":
                return await raydium_pool_by_lp(**tool_input)
            case "raydium_pool_by_mint":
                return await raydium_pool_by_mint(**tool_input)
            case "raydium_pool_keys":
                return await raydium_pool_keys(**tool_input)
            case "raydium_pool_liquidity_history":
                return await raydium_pool_liquidity_history(**tool_input)
            case "raydium_pool_position_history":
                return await raydium_pool_position_history(**tool_input)
            case "raydium_mint_list":
                return await raydium_mint_list()
            case "raydium_mint_info":
                return await raydium_mint_info(**tool_input)
            case "raydium_mint_price":
                return await raydium_mint_price(**tool_input)
            case "raydium_farm_info":
                return await raydium_farm_info(**tool_input)
            case "raydium_farm_by_lp":
                return await raydium_farm_by_lp(**tool_input)
            case "raydium_farm_keys":
                return await raydium_farm_keys(**tool_input)
            case "raydium_ido_keys":
                return await raydium_ido_keys(**tool_input)
            case "raydium_info":
                return await raydium_info()
            case "raydium_auto_fee":
                return await raydium_auto_fee()
            case "raydium_clmm_config":
                return await raydium_clmm_config()
            case "raydium_cpmm_config":
                return await raydium_cpmm_config()
            case "raydium_rpcs":
                return await raydium_rpcs()
            case "raydium_chain_time":
                return await raydium_chain_time()
            case "raydium_stake_pools":
                return await raydium_stake_pools()
            case "raydium_migrate_lp":
                return await raydium_migrate_lp()
            case "raydium_version":
                return await raydium_version()
            # Orca
            case "orca_pools":
                return await orca_pools(**tool_input)
            case "orca_pools_search":
                return await orca_pools_search(**tool_input)
            case "orca_pool_by_address":
                return await orca_pool_by_address(**tool_input)
            case "orca_locked_liquidity":
                return await orca_locked_liquidity(**tool_input)
            case "orca_protocol_stats":
                return await orca_protocol_stats()
            case "orca_protocol_token":
                return await orca_protocol_token()
            case "orca_circulating_supply":
                return await orca_circulating_supply()
            case "orca_total_supply":
                return await orca_total_supply()
            case "orca_tokens":
                return await orca_tokens(**tool_input)
            case "orca_tokens_search":
                return await orca_tokens_search(**tool_input)
            case "orca_token_by_mint":
                return await orca_token_by_mint(**tool_input)
            # Kamino
            case "kamino_strategies":
                return await kamino_strategies(**tool_input)
            case "kamino_lending":
                return await kamino_lending()
            case "kamino_markets":
                return await kamino_markets()
            case "kamino_market_reserves":
                return await kamino_market_reserves(**tool_input)
            case "kamino_oracle_prices":
                return await kamino_oracle_prices()
            case "kamino_earn_vaults":
                return await kamino_earn_vaults(**tool_input)
            case "kamino_staking_yields":
                return await kamino_staking_yields()
            case "kamino_staking_yields_mean":
                return await kamino_staking_yields_mean()
            case "kamino_leverage_stats":
                return await kamino_leverage_stats(**tool_input)
            case "kamino_user_positions":
                return await kamino_user_positions(**tool_input)
            case "kamino_user_obligations":
                return await kamino_user_obligations(**tool_input)
            case "kamino_epoch_info":
                return await kamino_epoch_info()
            # Marinade
            case "marinade_stats":
                return await marinade_stats()
            case "marinade_validators":
                return await marinade_validators(**tool_input)
            case "marinade_validator_scores":
                return await marinade_validator_scores(**tool_input)
            case "marinade_cluster_stats":
                return await marinade_cluster_stats(**tool_input)
            case "marinade_epoch_rewards":
                return await marinade_epoch_rewards()
            case "marinade_staking_report":
                return await marinade_staking_report()
            case "marinade_score_breakdown":
                return await marinade_score_breakdown(**tool_input)
            case "marinade_validator_uptimes":
                return await marinade_validator_uptimes(**tool_input)
            case "marinade_validator_commissions":
                return await marinade_validator_commissions(**tool_input)
            case "marinade_validator_versions":
                return await marinade_validator_versions(**tool_input)
            case "marinade_scoring_reports":
                return await marinade_scoring_reports()
            case "marinade_commission_changes":
                return await marinade_commission_changes()
            case "marinade_config":
                return await marinade_config()
            case "marinade_native_apy":
                return await marinade_native_apy(**tool_input)
            case "marinade_msol_apy":
                return await marinade_msol_apy(**tool_input)
            case "marinade_jito_commissions":
                return await marinade_jito_commissions(**tool_input)
            case "marinade_msol_votes":
                return await marinade_msol_votes(**tool_input)
            case "marinade_vemnde_votes":
                return await marinade_vemnde_votes(**tool_input)
            case "marinade_wallet_msol_balance":
                return await marinade_wallet_msol_balance(**tool_input)
            case "marinade_wallet_vemnde_balance":
                return await marinade_wallet_vemnde_balance(**tool_input)
            case "marinade_wallet_native_stake":
                return await marinade_wallet_native_stake(**tool_input)
            # Solend
            case "solend_markets":
                return await solend_markets(**tool_input)
            case "solend_reserves":
                return await solend_reserves(**tool_input)
            case "solend_user_overview":
                return await solend_user_overview(**tool_input)
            case "solend_stats":
                return await solend_stats()
            case "solend_daily_fees":
                return await solend_daily_fees(**tool_input)
            case "solend_lst_rates":
                return await solend_lst_rates()
            case "solend_prices":
                return await solend_prices(**tool_input)
            case "solend_historical_prices":
                return await solend_historical_prices(**tool_input)
            case "solend_reserves_history":
                return await solend_reserves_history(**tool_input)
            case "solend_reserves_config_changes":
                return await solend_reserves_config_changes(**tool_input)
            case "solend_daily_stats":
                return await solend_daily_stats(**tool_input)
            case "solend_circulating_supply":
                return await solend_circulating_supply()
            case "solend_total_supply":
                return await solend_total_supply()
            case "solend_save_metrics":
                return await solend_save_metrics()
            case "solend_save_price_chart":
                return await solend_save_price_chart(**tool_input)
            case "solend_save_revenue_chart":
                return await solend_save_revenue_chart(**tool_input)
            case "solend_isolated_pool_stats":
                return await solend_isolated_pool_stats()
            case "solend_announcements":
                return await solend_announcements(**tool_input)
            case "solend_changelogs":
                return await solend_changelogs(**tool_input)
            case "solend_reward_stats":
                return await solend_reward_stats(**tool_input)
            case "solend_reward_score":
                return await solend_reward_score(**tool_input)
            case "solend_reward_proofs":
                return await solend_reward_proofs(**tool_input)
            case "solend_additional_emissions":
                return await solend_additional_emissions()
            case "solend_confirmed_rewards":
                return await solend_confirmed_rewards(**tool_input)
            case "solend_history":
                return await solend_history(**tool_input)
            case "solend_history_v2":
                return await solend_history_v2(**tool_input)
            case "solend_ctoken_history":
                return await solend_ctoken_history(**tool_input)
            case "solend_liquidation_attempts":
                return await solend_liquidation_attempts(**tool_input)
            case "solend_margin_trading_history":
                return await solend_margin_trading_history(**tool_input)
            case "solend_snapshot":
                return await solend_snapshot(**tool_input)
            case "solend_transactions":
                return await solend_transactions(**tool_input)
            case "solend_margin_trading_transactions":
                return await solend_margin_trading_transactions(**tool_input)
            case "solend_transaction_notes":
                return await solend_transaction_notes(**tool_input)
            case "solend_vip_eligibility":
                return await solend_vip_eligibility(**tool_input)
            case "solend_airdrops":
                return await solend_airdrops(**tool_input)
            case "solend_airdrops_jito":
                return await solend_airdrops_jito(**tool_input)
            case "solend_obligations_filtered":
                return await solend_obligations_filtered(**tool_input)
            case "solend_squeezy_obligations":
                return await solend_squeezy_obligations(**tool_input)
            case "solend_notifications":
                return await solend_notifications(**tool_input)
            case "solend_tokens_all":
                return await solend_tokens_all(**tool_input)
            case "solend_tokens":
                return await solend_tokens(**tool_input)
            case "solend_referral_payments":
                return await solend_referral_payments()
            case "solend_referral_attributed_payments":
                return await solend_referral_attributed_payments(**tool_input)
            case "solend_referral_referrer":
                return await solend_referral_referrer(**tool_input)
            case "solend_referral_referred":
                return await solend_referral_referred(**tool_input)
            case "solend_referral_stats":
                return await solend_referral_stats()
            case "solend_points":
                return await solend_points()
            case "solend_points_leaderboard":
                return await solend_points_leaderboard()
            case "solend_points_config":
                return await solend_points_config()
            case "solend_points_total":
                return await solend_points_total()
            case "solend_points_adjustments":
                return await solend_points_adjustments()
            # Jito (kobe — no auth)
            case "jito_stake_pool_stats":
                return await jito_stake_pool_stats()
            case "jito_jitosol_ratio":
                return await jito_jitosol_ratio()
            case "jito_validators":
                return await jito_validators(**tool_input)
            case "jito_preferred_validators":
                return await jito_preferred_validators(**tool_input)
            # Jito (gateway)
            case "jito_tip_floor":
                return await jito_tip_floor(jwt_token)
            case "jito_mev_rewards":
                return await jito_mev_rewards()
            case "jito_daily_mev":
                return await jito_daily_mev(**tool_input)
            case "jito_stake_growth":
                return await jito_stake_growth()
            case "jito_mev_commission":
                return await jito_mev_commission()
            # Helius RPC
            case "helius_token_holders":
                return await helius_token_holders(**tool_input)
            case "helius_token_supply":
                return await helius_token_supply(**tool_input)
            case "helius_wallet_tokens":
                return await helius_wallet_tokens(**tool_input)
            case "helius_wallet_txs":
                return await helius_wallet_txs(**tool_input)
            # Security
            case "token_security":
                return await token_security(jwt=jwt_token, **tool_input)
            # Portfolio
            case "wallet_balance":
                return await wallet_balance(jwt_token)
            case "user_transactions":
                return await user_transactions(jwt_token)
            case "limit_orders":
                return await limit_orders(jwt_token, **tool_input)
            case "dca_orders":
                return await dca_orders(jwt_token, **tool_input)
            # Birdeye (requires BIRDEYE_API_KEY in .env)
            case name if name.startswith("birdeye_"):
                if not BIRDEYE_KEY:
                    return {"error": "BIRDEYE_API_KEY not set in .env — add it to enable Birdeye tools"}
                match name:
                    case "birdeye_price":
                        return await birdeye_price(**tool_input)
                    case "birdeye_multi_price":
                        return await birdeye_multi_price(**tool_input)
                    case "birdeye_ohlcv":
                        return await birdeye_ohlcv(**tool_input)
                    case "birdeye_ohlcv_v1":
                        return await birdeye_ohlcv_v1(**tool_input)
                    case "birdeye_ohlcv_pair_v1":
                        return await birdeye_ohlcv_pair_v1(**tool_input)
                    case "birdeye_ohlcv_pair":
                        return await birdeye_ohlcv_pair(**tool_input)
                    case "birdeye_ohlcv_base_quote":
                        return await birdeye_ohlcv_base_quote(**tool_input)
                    case "birdeye_history_price":
                        return await birdeye_history_price(**tool_input)
                    case "birdeye_price_at_time":
                        return await birdeye_price_at_time(**tool_input)
                    case "birdeye_price_volume":
                        return await birdeye_price_volume(**tool_input)
                    case "birdeye_price_volume_multi":
                        return await birdeye_price_volume_multi(**tool_input)
                    case "birdeye_token_overview":
                        return await birdeye_token_overview(**tool_input)
                    case "birdeye_token_metadata":
                        return await birdeye_token_metadata(**tool_input)
                    case "birdeye_token_metadata_multi":
                        return await birdeye_token_metadata_multi(**tool_input)
                    case "birdeye_token_market_data":
                        return await birdeye_token_market_data(**tool_input)
                    case "birdeye_token_market_data_multi":
                        return await birdeye_token_market_data_multi(**tool_input)
                    case "birdeye_token_trade_data":
                        return await birdeye_token_trade_data(**tool_input)
                    case "birdeye_token_trade_data_multi":
                        return await birdeye_token_trade_data_multi(**tool_input)
                    case "birdeye_exit_liquidity":
                        return await birdeye_exit_liquidity(**tool_input)
                    case "birdeye_exit_liquidity_multi":
                        return await birdeye_exit_liquidity_multi(**tool_input)
                    case "birdeye_pair_overview":
                        return await birdeye_pair_overview(**tool_input)
                    case "birdeye_pair_overview_multi":
                        return await birdeye_pair_overview_multi(**tool_input)
                    case "birdeye_price_stats":
                        return await birdeye_price_stats(**tool_input)
                    case "birdeye_price_stats_multi":
                        return await birdeye_price_stats_multi(**tool_input)
                    case "birdeye_token_list":
                        return await birdeye_token_list(**tool_input)
                    case "birdeye_token_list_scroll":
                        return await birdeye_token_list_scroll(**tool_input)
                    case "birdeye_tokenlist_v1":
                        return await birdeye_tokenlist_v1(**tool_input)
                    case "birdeye_new_listings":
                        return await birdeye_new_listings(**tool_input)
                    case "birdeye_token_markets":
                        return await birdeye_token_markets(**tool_input)
                    case "birdeye_token_txs":
                        return await birdeye_token_txs(**tool_input)
                    case "birdeye_txs_all":
                        return await birdeye_txs_all(**tool_input)
                    case "birdeye_txs_recent":
                        return await birdeye_txs_recent(**tool_input)
                    case "birdeye_token_txs_v1":
                        return await birdeye_token_txs_v1(**tool_input)
                    case "birdeye_pair_txs":
                        return await birdeye_pair_txs(**tool_input)
                    case "birdeye_token_txs_by_time":
                        return await birdeye_token_txs_by_time(**tool_input)
                    case "birdeye_pair_txs_by_time":
                        return await birdeye_pair_txs_by_time(**tool_input)
                    case "birdeye_trader_txs":
                        return await birdeye_trader_txs(**tool_input)
                    case "birdeye_token_txs_by_volume":
                        return await birdeye_token_txs_by_volume(**tool_input)
                    case "birdeye_mint_burn_txs":
                        return await birdeye_mint_burn_txs(**tool_input)
                    case "birdeye_wallet_current_net_worth":
                        return await birdeye_wallet_current_net_worth(**tool_input)
                    case "birdeye_wallet_net_worth_history":
                        return await birdeye_wallet_net_worth_history(**tool_input)
                    case "birdeye_wallet_net_worth_multi":
                        return await birdeye_wallet_net_worth_multi(**tool_input)
                    case "birdeye_wallet_net_worth_details":
                        return await birdeye_wallet_net_worth_details(**tool_input)
                    case "birdeye_wallet_pnl_summary":
                        return await birdeye_wallet_pnl_summary(**tool_input)
                    case "birdeye_wallet_pnl_details":
                        return await birdeye_wallet_pnl_details(**tool_input)
                    case "birdeye_wallet_pnl_token":
                        return await birdeye_wallet_pnl_token(**tool_input)
                    case "birdeye_wallet_pnl_multi":
                        return await birdeye_wallet_pnl_multi(**tool_input)
                    case "birdeye_wallet_first_funded":
                        return await birdeye_wallet_first_funded(**tool_input)
                    case "birdeye_token_top_traders":
                        return await birdeye_token_top_traders(**tool_input)
                    case "birdeye_trader_gainers_losers":
                        return await birdeye_trader_gainers_losers(**tool_input)
                    case "birdeye_wallet_supported_chains":
                        return await birdeye_wallet_supported_chains(**tool_input)
                    case "birdeye_wallet_tx_list":
                        return await birdeye_wallet_tx_list(**tool_input)
                    case "birdeye_wallet_token_list":
                        return await birdeye_wallet_token_list(**tool_input)
                    case "birdeye_token_holders":
                        return await birdeye_token_holders(**tool_input)
                    case "birdeye_holder_batch":
                        return await birdeye_holder_batch(**tool_input)
                    case "birdeye_holder_distribution":
                        return await birdeye_holder_distribution(**tool_input)
                    case "birdeye_holder_profile":
                        return await birdeye_holder_profile(**tool_input)
                    case "birdeye_holder_positions":
                        return await birdeye_holder_positions(**tool_input)
                    case "birdeye_networks":
                        return await birdeye_networks(**tool_input)
                    case "birdeye_latest_block":
                        return await birdeye_latest_block(**tool_input)
                    case "birdeye_token_creation_info":
                        return await birdeye_token_creation_info(**tool_input)
                    case "birdeye_token_trending":
                        return await birdeye_token_trending(**tool_input)
                    case "birdeye_meme_token_detail":
                        return await birdeye_meme_token_detail(**tool_input)
                    case "birdeye_meme_token_list":
                        return await birdeye_meme_token_list(**tool_input)
                    case "birdeye_token_security":
                        return await birdeye_token_security(**tool_input)
                    case "birdeye_smart_money_tokens":
                        return await birdeye_smart_money_tokens(**tool_input)
                    case "birdeye_all_time_trades_single":
                        return await birdeye_all_time_trades_single(**tool_input)
                    case "birdeye_all_time_trades_multi":
                        return await birdeye_all_time_trades_multi(**tool_input)
                    case "birdeye_search":
                        return await birdeye_search(**tool_input)
                    case "birdeye_credits":
                        return await birdeye_credits(**tool_input)
                    case "birdeye_wallet_balance_change":
                        return await birdeye_wallet_balance_change(**tool_input)
                    case "birdeye_wallet_token_balance":
                        return await birdeye_wallet_token_balance(**tool_input)
                    case "birdeye_token_transfers":
                        return await birdeye_token_transfers(**tool_input)
                    case "birdeye_token_transfer_total":
                        return await birdeye_token_transfer_total(**tool_input)
                    case "birdeye_wallet_transfers":
                        return await birdeye_wallet_transfers(**tool_input)
                    case "birdeye_wallet_transfer_total":
                        return await birdeye_wallet_transfer_total(**tool_input)
                    case "birdeye_wallet_single_token_balance":
                        return await birdeye_wallet_single_token_balance(**tool_input)
                    case _:
                        return {"error": f"Unknown Birdeye tool: {name}"}
            case (
                "tensor_mint_proof"
                | "tensor_mint_collection"
                | "tensor_mint_active_listings"
                | "tensor_mint_activity"
                | "tensor_collections_browse"
                | "tensor_find_collection"
                | "tensor_search_collections"
                | "tensor_collections_mints"
                | "tensor_collections_list"
                | "tensor_collections_traits"
                | "tensor_collections_coll_bids"
                | "tensor_collections_nft_bids"
                | "tensor_collections_trait_bids"
                | "tensor_collections_pools"
                | "tensor_collections_tamm_pools"
                | "tensor_collections_tx_history"
                | "tensor_refresh_metadata"
                | "tensor_whitelist_info"
                | "tensor_trait_bid_attributes"
                | "tensor_refresh_rarities"
                | "tensor_edit_bid"
            ):
                if not TENSOR_KEY:
                    return {"error": "TENSOR_API_KEY not set in .env — add it to enable Tensor tools"}
                fn_map = {
                    "tensor_mint_proof": tensor_mint_proof,
                    "tensor_mint_collection": tensor_mint_collection,
                    "tensor_mint_active_listings": tensor_mint_active_listings,
                    "tensor_mint_activity": tensor_mint_activity,
                    "tensor_collections_browse": tensor_collections_browse,
                    "tensor_find_collection": tensor_find_collection,
                    "tensor_search_collections": tensor_search_collections,
                    "tensor_collections_mints": tensor_collections_mints,
                    "tensor_collections_list": tensor_collections_list,
                    "tensor_collections_traits": tensor_collections_traits,
                    "tensor_collections_coll_bids": tensor_collections_coll_bids,
                    "tensor_collections_nft_bids": tensor_collections_nft_bids,
                    "tensor_collections_trait_bids": tensor_collections_trait_bids,
                    "tensor_collections_pools": tensor_collections_pools,
                    "tensor_collections_tamm_pools": tensor_collections_tamm_pools,
                    "tensor_collections_tx_history": tensor_collections_tx_history,
                    "tensor_refresh_metadata": tensor_refresh_metadata,
                    "tensor_whitelist_info": tensor_whitelist_info,
                    "tensor_trait_bid_attributes": tensor_trait_bid_attributes,
                    "tensor_refresh_rarities": tensor_refresh_rarities,
                    "tensor_edit_bid": tensor_edit_bid,
                }
                return await fn_map[tool_name](**tool_input)
            case _:
                return {"error": f"Unknown tool: {tool_name}"}
