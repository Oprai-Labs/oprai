"""Real-time alpha signals off the live index — smart-money buys, tracked-wallet
activity, fresh launches. Deliberately STATELESS and fast (the core group-by runs
in <0.1s on the live index) so the Telegram bot can poll every few seconds;
subscription state (who tracks which wallet, who enabled smart alerts) and delivery
live in the caller (the bot's own Postgres), NOT here.

Everything is keyed by `since_block`: the bot remembers the last block it alerted on
per subscription and asks "what's new since". That makes the feed idempotent and
gap-free across bot restarts."""
from __future__ import annotations

from . import ch, node

_ZERO = "0x0000000000000000000000000000000000000000"
# Robinhood Chain runs ~10 blocks/s → ~864k blocks/day. Used to flag a token as a
# fresh launch (first mint within the last day) without a timestamp round-trip.
_BLOCKS_PER_DAY = 864_000

# Base / quote assets — buying these is not an "alpha" signal (they're the money
# side of every trade), so they'd otherwise dominate the discovery feed. Excluded
# at query time so they never eat a LIMIT slot from a real memecoin signal.
_BASE_ASSETS = {
    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",  # USDG (quote)
    "0x0bd7d308f8e1639fab988df18a8011f41eacad73",  # WETH
}
# Symbols that are always base/stable — a post-resolution safety net for any base
# asset not pinned by address above (new stables, WBTC, etc.).
_BASE_SYMBOLS = {"USDG", "WETH", "ETH", "WBTC", "WETH", "USDC", "USDT",
                 "USDE", "DAI", "USDS", "FRAX"}


def _base_filter(col: str) -> str:
    """SQL fragment excluding base/quote assets from a token column."""
    inl = ",".join(f"'{a}'" for a in _BASE_ASSETS)
    return f"{col} NOT IN ({inl})"


async def index_tip() -> int:
    """Highest block the index has ingested — the bot's cursor ceiling."""
    return int(await ch.scalar("SELECT max(block_number) FROM rh.token_transfers") or 0)


async def _token_meta(tokens: list[str]) -> dict[str, dict]:
    """symbol / name (from the `contracts` decode table) + first_block (from
    `token_metrics`) for a small set of tokens. token_metrics carries NO symbol."""
    if not tokens:
        return {}
    inl = ",".join(f"'{t}'" for t in tokens)
    meta = {t: {"symbol": None, "name": None, "first_block": 0} for t in tokens}
    crows = await ch.q(
        f"SELECT address AS token, symbol, name FROM rh.contracts FINAL "
        f"WHERE address IN ({inl})")
    for r in crows:
        meta[r["token"]]["symbol"] = r.get("symbol") or None
        meta[r["token"]]["name"] = r.get("name") or None
    frows = await ch.q(
        f"SELECT token, first_block FROM rh.token_metrics WHERE token IN ({inl})")
    for r in frows:
        meta[r["token"]]["first_block"] = int(r.get("first_block") or 0)
    # Most launchpad/memecoin tokens aren't in the decode table → symbol still null.
    # Resolve those live from the node's ERC20 symbol() so alerts read "$PEPE".
    missing = [t for t, m in meta.items() if not m.get("symbol")]
    if missing:
        for t, sym in (await node.resolve_symbols(missing)).items():
            meta[t]["symbol"] = sym
    return meta


async def smart_buys(since_block: int, min_smart: int = 2, limit: int = 30) -> dict:
    """Tokens that SMART wallets bought since `since_block`, grouped — the discovery
    feed. Each row: distinct smart buyers, total smart buys, newest block/ts, symbol,
    and whether it's a fresh launch. The bot throttles/dedupes per (subscriber, token)
    on its side; this just reports the current window truthfully."""
    tip = await index_tip()
    rows = await ch.q(f"""
        SELECT tt.token AS token,
               uniqExact(sw.wallet) AS smart_buyers,
               count() AS buys,
               max(tt.block_number) AS last_block,
               max(tt.timestamp) AS last_ts
        FROM rh.token_transfers tt
        INNER JOIN rh.smart_wallets sw ON tt.to_addr = sw.wallet
        WHERE tt.kind='erc20' AND tt.block_number > {int(since_block)}
          AND tt.from_addr != '{_ZERO}' AND {_base_filter('tt.token')}
        GROUP BY tt.token
        HAVING smart_buyers >= {int(min_smart)}
        ORDER BY smart_buyers DESC, buys DESC
        LIMIT {int(limit)}""")
    meta = await _token_meta([r["token"] for r in rows])
    fresh_floor = tip - _BLOCKS_PER_DAY
    signals = []
    for r in rows:
        m = meta.get(r["token"], {})
        if (m.get("symbol") or "").upper() in _BASE_SYMBOLS:
            continue  # safety net for base assets not pinned by address
        fb = m.get("first_block") or 0
        signals.append({
            "token": r["token"],
            "symbol": m.get("symbol"),
            "name": m.get("name"),
            "smart_buyers": int(r["smart_buyers"]),
            "buys": int(r["buys"]),
            "last_block": int(r["last_block"]),
            "last_ts": r.get("last_ts"),
            "is_new_launch": bool(fb and fb >= fresh_floor),
        })
    return {"tip": tip, "since_block": int(since_block), "signals": signals}


async def wallet_recent_buys(wallet: str, since_block: int, limit: int = 20) -> dict:
    """A tracked wallet's recent BUYS since `since_block` — token symbol + amount +
    best-effort USD (daily price) + whether the wallet itself is flagged smart. The
    per-wallet lookup is index-key-fast; the bot polls this for each tracked wallet."""
    w = ch.addr(wallet)
    tip = await index_tip()
    is_smart = bool(await ch.scalar(
        f"SELECT count() FROM rh.smart_wallets WHERE wallet='{w}'"))
    # AGGREGATE per token (not per transfer) so a wallet buying a token across many
    # txs is ONE clean alert, and EXCLUDE base/quote assets (USDG/WETH) — buying the
    # money side of a trade is not a signal worth pinging a follower about.
    rows = await ch.q(f"""
        SELECT tt.token AS token, max(tt.block_number) AS block, max(tt.timestamp) AS ts,
               sum(toFloat64(tt.value)) AS amt, count() AS n
        FROM rh.token_transfers tt
        WHERE tt.to_addr='{w}' AND tt.kind='erc20'
          AND tt.block_number > {int(since_block)} AND tt.from_addr != '{_ZERO}'
          AND {_base_filter('tt.token')}
        GROUP BY tt.token
        ORDER BY block DESC LIMIT {int(limit)}""")
    toks = list({r["token"] for r in rows})
    meta = await _token_meta(toks)
    # best-effort USD: token's latest daily price
    price = {}
    if toks:
        inl = ",".join(f"'{t}'" for t in toks)
        prows = await ch.q(
            f"SELECT token, argMax(price_usd, timestamp) AS px FROM rh.token_prices "
            f"WHERE token IN ({inl}) GROUP BY token")
        price = {r["token"]: float(r.get("px") or 0) for r in prows}
    buys = []
    for r in rows:
        px = price.get(r["token"], 0.0)
        usd = (r["amt"] / 1e18) * px if px else None
        m = meta.get(r["token"], {})
        buys.append({
            "token": r["token"],
            "symbol": m.get("symbol"),
            "name": m.get("name"),
            "block": int(r["block"]),
            "ts": r.get("ts"),
            "amount": r["amt"] / 1e18,
            "usd": round(usd, 2) if usd is not None else None,
            "tx_count": int(r.get("n") or 1),
        })
    return {"tip": tip, "wallet": w, "wallet_is_smart": is_smart,
            "since_block": int(since_block), "buys": buys}


async def new_launches(since_block: int, with_smart_only: bool = False,
                       limit: int = 40) -> dict:
    """Tokens whose FIRST mint landed since `since_block` — brand-new launches. When
    `with_smart_only`, keep just the ones a smart wallet has already bought (the
    highest-signal fresh launches). Symbol + smart-buyer count included."""
    tip = await index_tip()
    rows = await ch.q(f"""
        SELECT token, min(block_number) AS first_block, min(timestamp) AS first_ts
        FROM rh.token_transfers
        WHERE kind='erc20' AND from_addr='{_ZERO}' AND block_number > {int(since_block)}
        GROUP BY token
        ORDER BY first_block DESC
        LIMIT {int(limit) * 4 if with_smart_only else int(limit)}""")
    toks = [r["token"] for r in rows]
    smart = {}
    if toks:
        inl = ",".join(f"'{t}'" for t in toks)
        srows = await ch.q(f"""
            SELECT tt.token AS token, uniqExact(sw.wallet) AS smart_buyers
            FROM rh.token_transfers tt INNER JOIN rh.smart_wallets sw ON tt.to_addr=sw.wallet
            WHERE tt.kind='erc20' AND tt.token IN ({inl}) AND tt.from_addr != '{_ZERO}'
            GROUP BY tt.token""")
        smart = {r["token"]: int(r["smart_buyers"]) for r in srows}
    meta = await _token_meta(toks)
    out = []
    for r in rows:
        sb = smart.get(r["token"], 0)
        if with_smart_only and sb == 0:
            continue
        m = meta.get(r["token"], {})
        out.append({
            "token": r["token"],
            "symbol": m.get("symbol"),
            "name": m.get("name"),
            "first_block": int(r["first_block"]),
            "first_ts": r.get("first_ts"),
            "smart_buyers": sb,
        })
        if len(out) >= limit:
            break
    return {"tip": tip, "since_block": int(since_block), "launches": out}
