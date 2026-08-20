"""
Token category service — authoritative answers to "which stablecoins / LSTs
/ blue-chips exist on Solana" questions.

Why this exists
---------------
Free-running LLMs answer category-listing questions inconsistently — they
list 2-3 canonical names and omit newer launches. Prompt rules don't survive
context drift. The fix is to compute the list deterministically server-side
and inject it into the LLM's context for the turn.

Sources, in preference order:
  - LST: Jupiter `/tokens/v2/tag?query=lst` — official tag, fully dynamic.
  - Blue-chip: Birdeye `/defi/v3/token/list` sorted by market_cap with a
    floor — top N by liquidity. Fully dynamic.
  - Stable: hybrid. No public API tags Solana stables, so we keep a small
    curated anchor list + auto-discover Jupiter-verified tokens whose symbol
    or name matches the USD-pegged shape. Combined and deduped.

Each result row carries:
    { symbol, mint, name, role }
The `role` field is a short human-readable description the LLM repeats in
its prose answer ("USDC — Most-used USD stable, issued by Circle").

Caching
-------
Each category has a TTL-cached payload. Token sets don't drift hour-to-hour;
1h cache is plenty for chat latency. Cache is process-local so service
restarts re-prime from upstream.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Read Jupiter API key directly from env — chat-service's pydantic-settings
# bundle doesn't model it (the key is owned by gateway / solana-service), but
# nothing stops us from optionally using it when present for higher rate
# limits on the category fetches.
_JUPITER_API_KEY = os.environ.get("JUPITER_API_KEY", "").strip() or None


@dataclass(frozen=True)
class CategoryToken:
    symbol: str
    mint: str
    name: str
    role: str  # human one-liner — what makes this token distinct


# ---------------------------------------------------------------------------
# Curated anchor lists
# ---------------------------------------------------------------------------
# Each anchor entry is a member we want guaranteed-present in the result set
# even when the dynamic source missed it (Jupiter tag lag, brand-new launch,
# upstream outage). The `mint` is the canonical Solana mint address.
# Refreshed manually when a major new token launches and the dynamic source
# hasn't picked it up yet — see services/scripts/refresh_token_categories.py
# (TODO: build the diff-PR tool).

STABLE_ANCHORS: tuple[CategoryToken, ...] = (
    CategoryToken("USDC", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                  "USD Coin", "Most-used USD stable, issued by Circle"),
    CategoryToken("USDT", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
                  "Tether USD", "Tether's USD stable, broad liquidity"),
    CategoryToken("USDS", "USDSwr9ApdHk5bvJKMjzff41FfuX8bSxdKcR81vTwcA",
                  "USDS", "Sky / former MakerDAO USD stable"),
    CategoryToken("PYUSD", "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo",
                  "PayPal USD", "PayPal's fiat-backed USD stable"),
    CategoryToken("FDUSD", "9zNQRsGLjNKwCUU5Gq5LR8beUCPzQMVMqKAi3SSZh54u",
                  "First Digital USD", "Regulated-in-Hong-Kong fiat-backed stable"),
    CategoryToken("DAI", "EjmyN6qEC1Tf1JxiG1ae7UTJhUxSwk1TCWNWqxWV4J6o",
                  "Dai", "Ethereum-bridged overcollateralized stable"),
    CategoryToken("USDH", "USDH1SM1ojwWUga67PGrgFWUHibbjqMvuMaDkRJTgkX",
                  "Hubble USDH", "Hubble's overcollateralized stable"),
    CategoryToken("USDY", "A1KLoBrKBde8Ty9qtNQUtq3C2ortoC3u7twggz7sEto6",
                  "Ondo US Dollar Yield", "Ondo yield-bearing tokenised T-bills"),
    # USDP, FRAX, TUSD are popular on Ethereum but have no first-class Solana
    # presence today — left out of the anchor list to avoid mint typos. The
    # auto-discovery pass will pick them up when Jupiter lists them.
)

LST_ANCHORS: tuple[CategoryToken, ...] = (
    CategoryToken("jitoSOL", "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
                  "Jito Staked SOL", "Jito's MEV-rewarded liquid stake"),
    CategoryToken("JupSOL", "jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v",
                  "Jupiter Staked SOL", "Jupiter's validator-set liquid stake"),
    CategoryToken("mSOL", "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
                  "Marinade Staked SOL", "Marinade's delegation-strategy liquid stake"),
    CategoryToken("bSOL", "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1",
                  "BlazeStake SOL", "BlazeStake's liquid stake"),
    CategoryToken("INF", "5oVNBeEEQvYi1cX3ir8Dx5n1P7pdxydbGF2X4TxVusJm",
                  "Sanctum Infinity", "Sanctum's multi-LST pool"),
    CategoryToken("stSOL", "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj",
                  "Lido Staked SOL", "Lido's Solana liquid stake (deprecated, withdraw-only)"),
)

# Strict whitelist for LST auto-discovery from Jupiter's `lst` tag. The tag
# is over-broad — it returns WSOL (Wrapped SOL is NOT staked), various
# wrapped variants (kSOL, pSOL, xSOL, …) and one-off CEX wrapped balances
# (bbSOL = BybitSOL) that aren't true LSTs. Add a symbol ONLY when it
# actually represents a stake-derivative product. Refreshed manually.
_LST_AUTO_DISCOVERY_WHITELIST: frozenset[str] = frozenset({
    "vSOL",      # Validator SOL (Stake.fish)
    "hSOL",      # Helius Staked SOL
    "ezSOL",     # Renzo Restaked SOL
    "fSOL",      # Fragmetric (LRT)
    "compassSOL", "picoSOL", "edgeSOL",
    "dSOL",      # Drift Staked SOL
    "laineSOL",
    "jucySOL",
})

# Symbols that MUST NOT appear in the LST list even if Jupiter's `lst`
# tag returns them. WSOL is the obvious one — wrapping isn't staking.
_LST_DENYLIST: frozenset[str] = frozenset({
    "SOL", "WSOL",
    "kySOL", "kSOL",        # Kyros wrapped, not a liquid-stake product
    "bbSOL",                # Bybit wrapped balance, not on-chain LST
    "meSOL",                # Magic Eden internal balance wrap
    "pSOL",                 # Parrot SOL (stable-issuer wrapped, not LST)
    "PSOL",                 # Phantom wrapped balance
    "xSOL", "rSOL",         # Generic restaked / reflect tokens not LSTs
    "bbsol", "psol",        # case variants
})

MEMECOIN_ANCHORS: tuple[CategoryToken, ...] = (
    CategoryToken("BONK", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
                  "Bonk", "OG Solana dog meme, broadest liquidity"),
    CategoryToken("WIF", "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
                  "dogwifhat", "Top-mcap meme with sustained volume"),
    CategoryToken("POPCAT", "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr",
                  "Popcat", "Pop-the-cat top-tier meme"),
    CategoryToken("PNUT", "2qEHjDLDLbuBgRYvsxhc5D6uDWAivNFZGan56P1tpump",
                  "Peanut the Squirrel", "Squirrel-themed launchpad winner"),
    CategoryToken("FARTCOIN", "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump",
                  "Fartcoin", "AI16Z-era meme, high volume"),
    CategoryToken("MEW", "MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5",
                  "cat in a dogs world", "Mid-cap cat meme"),
    CategoryToken("MOODENG", "ED5nyyWEzpPPiWimP8vYm7sD7TD3LAt3Q3gRTWHzPJBY",
                  "Moo Deng", "Pygmy hippo meme"),
    CategoryToken("CHILLGUY", "Df6yfrKC8kZE3KNkrHERKzAetSxbrWeniQfyJY4Jpump",
                  "Just a chill guy", "Vibe-meme, 2024 launch"),
)


BLUE_CHIP_ANCHORS: tuple[CategoryToken, ...] = (
    CategoryToken("SOL",  "So11111111111111111111111111111111111111112",
                  "Solana", "Native asset, gas + base trading pair"),
    CategoryToken("JUP",  "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
                  "Jupiter", "Jupiter aggregator governance token"),
    CategoryToken("JTO",  "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
                  "Jito", "Jito governance + MEV revenue token"),
    CategoryToken("BONK", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
                  "Bonk", "OG Solana meme, broad liquidity"),
    CategoryToken("WIF",  "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
                  "dogwifhat", "Top-mcap meme with sustained volume"),
    CategoryToken("PYTH", "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
                  "Pyth Network", "Pyth oracle governance token"),
    CategoryToken("RAY",  "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
                  "Raydium", "Raydium DEX governance"),
)


# Strict whitelist of symbols we accept from the auto-discovery pass. The
# Jupiter `verified` tag returns memes ("DOLLAR", "BDC", "USDCAT", "unstable
# coin"), bridged Wormhole wraps (USDCBS/ET/PO, USDTBS/ET/PO) and lending
# receipt tokens (JLUSDC, RAUSDS, PBUSDC) that we MUST NOT surface as first-
# class stables. The anchor list above covers the canonical set; this
# whitelist only adds NEW issuances that hadn't made it into the anchors yet
# (USDG, USDE, USD1 type launches). When something new gains traction, add
# it here explicitly — never broaden to a regex.
_STABLE_AUTO_DISCOVERY_WHITELIST: frozenset[str] = frozenset({
    "USDG",   # Global Dollar (Paxos / Stripe consortium)
    "USDE",   # Ethena USDe
    "USD1",   # World Liberty Financial USD
    "USD+",   # Overnight USD+
    "USDV",   # USDv
    "USDU",   # USDu
    "USDP",   # Pax Dollar
    "USDON",  # Ondo US Dollar Token
    "USD*",   # USD Star (Perena)
    "ISC",    # International Stable Currency
    "DUSD",   # DUSD
    "SUSD",   # Solayer USD
    "XUSD",   # StraitsX USD
})


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, list[CategoryToken]]] = {}
_CACHE_TTL_S = 3600.0  # 1 hour


def _cache_get(key: str) -> list[CategoryToken] | None:
    entry = _CACHE.get(key)
    if not entry:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL_S:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: list[CategoryToken]) -> None:
    _CACHE[key] = (time.time(), value)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

JUPITER_TAG_URL_PAID = "https://api.jup.ag/tokens/v2/tag"
JUPITER_TAG_URL_LITE = "https://lite-api.jup.ag/tokens/v2/tag"


async def get_category_tokens(category: str) -> list[CategoryToken]:
    """Return the canonical token set for a category. Cached 1h.

    `category` ∈ {"stable", "lst", "blue_chip"}. Unknown → empty list.
    Failures fall back to the anchor list so the LLM still gets a non-empty
    seed (better than no answer).
    """
    cached = _cache_get(category)
    if cached is not None:
        return cached

    if category == "lst":
        out = await _fetch_lsts()
    elif category == "stable":
        out = await _fetch_stables()
    elif category == "blue_chip":
        out = _fetch_blue_chips_anchor_only()
    elif category == "memecoin":
        out = await _fetch_memecoins()
    else:
        return []

    _cache_set(category, out)
    return out


async def _fetch_lsts() -> list[CategoryToken]:
    """LST tokens — anchors first, plus a strict whitelist of newer LSTs
    picked up from Jupiter's `lst` tag. Jupiter's tag is over-broad: it
    returns WSOL (wrapping is NOT staking), CEX-wrapped balances (bbSOL),
    and generic wrapped variants (kSOL / xSOL / pSOL). We deny those by
    symbol and only accept entries explicitly whitelisted.
    """
    base = JUPITER_TAG_URL_PAID if _JUPITER_API_KEY else JUPITER_TAG_URL_LITE
    headers = {"x-api-key": _JUPITER_API_KEY} if _JUPITER_API_KEY else {}
    discovered: dict[str, CategoryToken] = {a.mint: a for a in LST_ANCHORS}
    anchor_syms = {a.symbol for a in LST_ANCHORS}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(base, params={"query": "lst"}, headers=headers)
            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, list):
                    for row in payload:
                        sym = str(row.get("symbol") or "").strip()
                        mint = str(row.get("id") or row.get("address") or "").strip()
                        name = str(row.get("name") or sym or "").strip()
                        if not sym or not mint:
                            continue
                        if sym in anchor_syms:
                            continue
                        if sym in _LST_DENYLIST or sym.lower() in _LST_DENYLIST:
                            continue
                        if sym not in _LST_AUTO_DISCOVERY_WHITELIST:
                            continue
                        if mint in discovered:
                            continue
                        discovered[mint] = CategoryToken(
                            sym, mint, name,
                            role=f"{name} liquid staking token",
                        )
    except Exception as exc:  # noqa: BLE001 — fallback to anchors
        logger.warning("jupiter LST fetch failed: %s", exc)

    # Anchors first (canonical order), then auto-discovered tail (alphabetical).
    out: list[CategoryToken] = list(LST_ANCHORS)
    seen = {t.mint for t in out}
    tail = [t for t in discovered.values() if t.mint not in seen]
    tail.sort(key=lambda t: t.symbol.lower())
    out.extend(tail)
    return out


async def _fetch_stables() -> list[CategoryToken]:
    """Stable tokens — anchors first, plus an explicit whitelist of newer
    issuances picked up from Jupiter's verified list. We intentionally do
    NOT do regex matching against symbol/name: that pulls in memes
    ("DOLLAR", "BDC"), Wormhole portal wraps and lending receipt tokens
    that overload the LLM with garbage rows.
    """
    base = JUPITER_TAG_URL_PAID if _JUPITER_API_KEY else JUPITER_TAG_URL_LITE
    headers = {"x-api-key": _JUPITER_API_KEY} if _JUPITER_API_KEY else {}
    discovered: dict[str, CategoryToken] = {a.mint: a for a in STABLE_ANCHORS}
    anchor_symbols = {a.symbol.upper() for a in STABLE_ANCHORS}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(base, params={"query": "verified"}, headers=headers)
            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, list):
                    for row in payload:
                        sym = str(row.get("symbol") or "").strip().upper()
                        mint = str(row.get("id") or row.get("address") or "").strip()
                        name = str(row.get("name") or sym or "").strip()
                        if not sym or not mint:
                            continue
                        if sym in anchor_symbols:
                            continue
                        if sym not in _STABLE_AUTO_DISCOVERY_WHITELIST:
                            continue
                        if mint in discovered:
                            continue
                        discovered[mint] = CategoryToken(
                            sym, mint, name,
                            role=f"{name} (USD-pegged stablecoin)",
                        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("jupiter verified-tokens stable scan failed: %s", exc)

    # Anchors first (canonical order), then auto-discovered tail (alphabetical).
    out: list[CategoryToken] = list(STABLE_ANCHORS)
    seen = {t.mint for t in out}
    tail = [t for t in discovered.values() if t.mint not in seen]
    tail.sort(key=lambda t: t.symbol.lower())
    out.extend(tail)
    return out


async def _fetch_memecoins() -> list[CategoryToken]:
    """Memecoins — anchor list only. Jupiter has no `meme` tag and the
    `trending` endpoint returns utility tokens (JUP, JTO, PYTH) that
    aren't memes. We curate the anchor list manually and refresh
    periodically. Order = market-cap descending at curation time.
    """
    return list(MEMECOIN_ANCHORS)


def _fetch_blue_chips_anchor_only() -> list[CategoryToken]:
    """Blue-chip seed list. Anchor-only for now; Birdeye top-mcap fetch
    lives in chat-service Birdeye client but isn't wired here yet — anchors
    cover the well-known set the LLM is asked about ~99% of the time.
    """
    return list(BLUE_CHIP_ANCHORS)


# Patterns the IntentRouter uses to decide WHICH category the user named.
# Multilingual word fragments — matched against the user message lowercase.
# Not a full NLP — just enough to disambiguate stable vs LST vs blue-chip
# when the classifier flagged `is_category_request=true` without naming the

def format_category_context_block(category: str, tokens: list[CategoryToken]) -> str:
    """
    Build the system-message snippet injected into the LLM context when a
    category request is detected. Renders as authoritative — the LLM should
    treat these as the canonical set and not deviate.
    """
    if not tokens:
        return ""
    pretty = {"stable": "stablecoins", "lst": "liquid staking tokens",
              "blue_chip": "blue-chip tokens",
              "memecoin": "memecoins"}.get(category, category)
    lines = [
        f"[Category context — authoritative, this is your data source]",
        f"The user is asking about Solana {pretty}. You have NO tools",
        f"available this turn — answer in PROSE using the list below.",
        f"",
        f"FORBIDDEN: refusing the question, hedging, asking for clarification,",
        f"saying \"I cannot access\", \"I need more information\", \"my system",
        f"does not have direct access to this data\", or any equivalent in any",
        f"language. The list IS the data. Just answer in the user's language.",
        f"",
        f"Response shape:",
        f"  - One short intro sentence in the user's language.",
        f"  - One bullet per token below, in the order shown, formatted",
        f"    `**SYMBOL** — short role description`.",
        f"  - One closing sentence telling the user they can ask to swap",
        f"    into any of these by naming it.",
        f"",
        f"The list:",
    ]
    for t in tokens:
        lines.append(f"- {t.symbol} — {t.role}")
    return "\n".join(lines)
