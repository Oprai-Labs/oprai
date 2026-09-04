"""Protocol positions — what a wallet has parked INSIDE protocols on Robinhood Chain,
read straight from our node, so `total_usd` is the wallet's whole balance and not
just what sits in its own address.

Method (proven read-by-read on live data before this was written):
  * Uniswap V4 LP — the PositionManager is NOT ERC721Enumerable, so the INDEX lists
    the position NFTs a wallet still owns; the NODE reads each one
    (getPoolAndPositionInfo + getPositionLiquidity) and the pool's slot0 via
    PoolManager.extsload(keccak(poolId ‖ 6)); Uniswap math turns liquidity into
    token amounts.
  * Uniswap V3 LP — same idea with positions(tokenId) + factory.getPool + slot0.
  * Morpho Blue — position(id, user) shares × market(id) totals → supplied /
    borrowed assets + raw collateral.
  * ERC-4626 vaults (steakUSDG, spUSDG, …) — balanceOf → convertToAssets → asset.
  * Staking gauges / pool tokens (Ramses vAMM, cowUniswap…) — balanceOf, then the
    pool token is valued generically: the pool contract's own token balances × the
    user's share of totalSupply (getReserves when it exists).
  * Lighter perps — the public account API by L1 address (collateral is off-chain
    on Lighter's own system, so the node can't see it).
Every contract set (vaults, gauges, lockers, Morpho markets, Ramses pairs) comes from
`protocol_registry`, built from on-chain EVENTS by build_protocols.py — nothing is a
hand-written address list except the handful of singletons named below."""
from __future__ import annotations

import asyncio
import json
import math
from functools import lru_cache

import httpx
from eth_hash.auto import keccak

from . import ch, node

# ── singletons (verified on-chain this session) ────────────────────────────
V3_NPM = "0x73991a25c818bf1f1128deaab1492d45638de0d3"
V4_POSM = "0x58daec3116aae6d93017baaea7749052e8a04fa7"
V4_PM = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
MORPHO = "0x9d53d5e3bd5e8d4cbfa6db1ca238aea02e651010"
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
USDE = "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34"
WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
ZERO = "0x0000000000000000000000000000000000000000"
DEAD = "0x000000000000000000000000000000000000dead"
LIGHTER_BASE = "https://api.rh.lighter.xyz"
Q96 = 2 ** 96
STABLES = {USDG, USDE}


def sel(sig: str) -> str:
    return "0x" + keccak(sig.encode()).hex()[:8]


S = {k: sel(k) for k in (
    "balanceOf(address)", "decimals()", "totalSupply()", "getReserves()", "token0()", "token1()",
    "positions(uint256)", "factory()", "getPool(address,address,uint24)", "slot0()",
    "getPoolAndPositionInfo(uint256)", "getPositionLiquidity(uint256)", "extsload(bytes32)",
    "market(bytes32)", "position(bytes32,address)", "idToMarketParams(bytes32)",
    "convertToAssets(uint256)", "asset()", "earned(address)", "price()", "liquidity()", "positions(bytes32)",
)}


def _u(n: int) -> str:
    return hex(n)[2:].rjust(64, "0")


def _a(addr: str) -> str:
    return addr.lower().replace("0x", "").rjust(64, "0")


def _w(hx: str, i: int) -> int:
    hx = hx[2:] if hx.startswith("0x") else hx
    s = hx[i * 64:(i + 1) * 64]
    return int(s, 16) if s else 0


def _addr(hx: str, i: int) -> str:
    hx = hx[2:] if hx.startswith("0x") else hx
    return "0x" + hx[i * 64 + 24:(i + 1) * 64].lower()


def _s24(v: int) -> int:
    v &= (1 << 24) - 1
    return v - (1 << 24) if v >= (1 << 23) else v


async def _call(to: str, data: str) -> str:
    try:
        return await node.eth_call(to, data)
    except Exception:
        return "0x"


_dec_cache: dict[str, int] = {ZERO: 18, USDG: 6, WETH: 18}


async def decimals(token: str) -> int:
    t = token.lower()
    if t in _dec_cache:
        return _dec_cache[t]
    r = await _call(t, S["decimals()"])
    d = _w(r, 0) if r not in ("0x", "") else 18
    _dec_cache[t] = d if 0 < d <= 36 else 18
    return _dec_cache[t]


_ts_cache: dict[str, float] = {}


async def total_supply(token: str) -> float:
    t = token.lower()
    if t not in _ts_cache:
        raw = _w(await _call(t, S["totalSupply()"]), 0)
        _ts_cache[t] = raw / 10 ** (await decimals(t)) if raw else float("inf")
    return _ts_cache[t]


# ── pricing ────────────────────────────────────────────────────────────────
async def eth_usd() -> float:
    return float(await ch.scalar("SELECT argMax(weth_usd, day) FROM rh.weth_price") or 0.0)


_px_cache: dict[str, float | None] = {}
_px_source: dict[str, str] = {}   # token → "index" | "index-stale(Nd)" | "pool" | "stable" | "weth"
STALE_DAYS = 3


async def price_usd(token: str) -> float | None:
    """token_prices first; stables = 1; ETH/WETH = weth_price; else the deepest
    live pool against USDG/WETH (slot0 read from the node). None only when the
    token has no market at all."""
    t = token.lower()
    if t in STABLES:
        return 1.0
    if t in (ZERO, WETH):
        return await eth_usd()
    if t in _px_cache:
        return _px_cache[t]
    row = await ch.one(
        f"SELECT argMax(price_usd, timestamp) AS px, dateDiff('day', max(toDateTime(timestamp)), now()) AS age "
        f"FROM rh.token_prices WHERE token='{t}'")
    px, age = (row or {}).get("px"), int((row or {}).get("age") or 0)
    val, src = (float(px) if px and float(px) > 0 else None), "index"
    if val is None or age > STALE_DAYS:
        live = await _pool_price_usd(t)
        if live is not None:
            val, src = live, "pool"
        elif val is not None:
            val, src = None, f"index-stale({age}d)-no-live-market"
    _px_cache[t], _px_source[t] = val, src
    return val


MIN_POOL_TVL_USD = 300.0  # below this a pool is not a market and must not set a price


async def _pool_price_usd(t: str) -> float | None:
    """Price from the deepest LIVE USDG/WETH pool of this token (V4 slot0 by poolId via
    extsload, V3 slot0() by address). A pool with no liquidity or under
    MIN_POOL_TVL_USD is ignored — a dead pool's sqrtPrice is not a price."""
    quotes = "','".join(STABLES | {WETH})
    rows = await ch.q(
        f"SELECT pool, token0, token1, dex FROM rh.dex_pools WHERE "
        f"(token0='{t}' AND token1 IN ('{quotes}')) OR (token1='{t}' AND token0 IN ('{quotes}')) "
        f"LIMIT 12", timeout=20)
    best: tuple[float, float] | None = None
    for r in rows:
        if r["dex"] == "uniswap-v4":
            base = int.from_bytes(keccak(bytes.fromhex(r["pool"][2:]) + (6).to_bytes(32, "big")), "big")
            raw = _w(await _call(V4_PM, S["extsload(bytes32)"] + _u(base)), 0)
            liq = _w(await _call(V4_PM, S["extsload(bytes32)"] + _u((base + 3) % 2 ** 256)), 0)
        elif r["dex"] == "uniswap-v3":
            raw = _w(await _call(r["pool"], S["slot0()"]), 0)
            liq = _w(await _call(r["pool"], S["liquidity()"]), 0)
        else:
            continue
        sqrt_p = raw & ((1 << 160) - 1)
        if not sqrt_p or not liq:
            continue
        t0, t1 = r["token0"].lower(), r["token1"].lower()
        d0, d1 = await decimals(t0), await decimals(t1)
        p0_in_1 = _pool_price_from_sqrt(sqrt_p, d0, d1)
        if t0 == t:
            q = await price_usd(t1)
            usd = p0_in_1 * q if q else None
            tvl = 2 * liq * sqrt_p / Q96 / 10 ** d1 * (q or 0)          # quote = token1
        else:
            q = await price_usd(t0)
            usd = (q / p0_in_1) if (q and p0_in_1) else None
            tvl = 2 * liq * Q96 / sqrt_p / 10 ** d0 * (q or 0)          # quote = token0
        if usd and tvl >= MIN_POOL_TVL_USD and (best is None or tvl > best[0]):
            best = (tvl, usd)
    return best[1] if best else None


def _pool_price_from_sqrt(sqrt_p: int, d0: int, d1: int) -> float:
    """token0 price in token1 units from sqrtPriceX96."""
    return (sqrt_p / Q96) ** 2 * 10 ** (d0 - d1)


async def _value_pair(t0: str, t1: str, amt0: float, amt1: float,
                      sqrt_p: int | None) -> tuple[float | None, list[str]]:
    """USD of a (amt0, amt1) leg pair → (usd, unpriced_legs). If one side has no price
    but the other is a real quote asset, derive it from THIS pool's sqrtPrice (the
    position's own pool is its market). A non-zero leg nobody can price stays
    unpriced and is listed — never guessed."""
    p0, p1 = await price_usd(t0), await price_usd(t1)
    quote = STABLES | {WETH, ZERO}
    if sqrt_p and p0 is None and p1 is not None and t1 in quote:
        p0 = _pool_price_from_sqrt(sqrt_p, await decimals(t0), await decimals(t1)) * p1
    if sqrt_p and p1 is None and p0 is not None and t0 in quote:
        p1 = p0 / _pool_price_from_sqrt(sqrt_p, await decimals(t0), await decimals(t1))
    for t, a in ((t0, amt0), (t1, amt1)):
        if a > 0 and t not in quote and a > await total_supply(t):
            if t == t0:
                p0 = None
            else:
                p1 = None
            _px_source[t] = "broken-token(balance>totalSupply)"
    unpriced = [t for t, p, a in ((t0, p0, amt0), (t1, p1, amt1)) if p is None and a > 0]
    if (p0 is None and amt0 > 0) and (p1 is None and amt1 > 0):
        return None, unpriced
    return amt0 * (p0 or 0) + amt1 * (p1 or 0), unpriced


def _amounts(L: int, sqrt_p: int, tl: int, tu: int) -> tuple[int, int]:
    sa = int(math.sqrt(1.0001 ** tl) * Q96)
    sb = int(math.sqrt(1.0001 ** tu) * Q96)
    if sqrt_p <= sa:
        return L * (sb - sa) * Q96 // (sa * sb), 0
    if sqrt_p >= sb:
        return 0, L * (sb - sa) // Q96
    return L * (sb - sqrt_p) * Q96 // (sqrt_p * sb), L * (sqrt_p - sa) // Q96


# ── enumeration from the index ─────────────────────────────────────────────
async def _owned_nfts(wallet: str, nft: str, limit: int = 300) -> list[int]:
    """Position NFT ids the wallet still owns = ids whose LAST transfer went to it."""
    rows = await ch.q(f"""
        SELECT token_id, argMax(to_addr, (block_number, log_index)) AS owner
        FROM rh.token_transfers
        WHERE token='{nft}' AND kind='erc721' AND token_id IN (
            SELECT DISTINCT token_id FROM rh.token_transfers
            WHERE token='{nft}' AND kind='erc721' AND to_addr='{wallet}'
            ORDER BY block_number DESC LIMIT {limit})
        GROUP BY token_id""", timeout=60)
    return [int(r["token_id"]) for r in rows if r["owner"] == wallet]


async def _registry(kind: str) -> list[dict]:
    try:
        return await ch.q(f"SELECT address, protocol, meta FROM rh.protocol_registry WHERE kind='{kind}'")
    except Exception:
        return []


# ── adapters ───────────────────────────────────────────────────────────────
async def uniswap_v4(wallet: str) -> list[dict]:
    out = []
    for tid in await _owned_nfts(wallet, V4_POSM):
        L = _w(await _call(V4_POSM, S["getPositionLiquidity(uint256)"] + _u(tid)), 0)
        if not L:
            continue
        pi = await _call(V4_POSM, S["getPoolAndPositionInfo(uint256)"] + _u(tid))
        if len(pi) < 2 + 64 * 6:
            continue
        c0, c1 = _addr(pi, 0), _addr(pi, 1)
        fee, ts, hooks = _w(pi, 2), _s24(_w(pi, 3)), _addr(pi, 4)
        info = _w(pi, 5)
        tl, tu = _s24((info >> 8) & 0xFFFFFF), _s24((info >> 32) & 0xFFFFFF)
        key = bytes.fromhex(_a(c0) + _a(c1) + _u(fee) + (ts & (2 ** 256 - 1)).to_bytes(32, "big").hex() + _a(hooks))
        pid = keccak(key)
        slot = keccak(pid + (6).to_bytes(32, "big"))
        raw = _w(await _call(V4_PM, S["extsload(bytes32)"] + slot.hex()), 0)
        sqrt_p = raw & ((1 << 160) - 1)
        if not sqrt_p:
            continue
        a0, a1 = _amounts(L, sqrt_p, tl, tu)
        d0, d1 = await decimals(c0), await decimals(c1)
        usd, unpriced = await _value_pair(c0, c1, a0 / 10 ** d0, a1 / 10 ** d1, sqrt_p)
        out.append({"protocol": "Uniswap V4", "kind": "lp", "id": tid,
                    "token0": c0, "token1": c1, "amount0": a0 / 10 ** d0, "amount1": a1 / 10 ** d1,
                    "fee": fee, "hooks": hooks if int(hooks, 16) else None,
                    "usd": round(usd, 2) if usd is not None else None, "unpriced": unpriced})
    return out


async def uniswap_v3(wallet: str) -> list[dict]:
    out = []
    ids = await _owned_nfts(wallet, V3_NPM)
    if not ids:
        return out
    factory = _addr(await _call(V3_NPM, S["factory()"]), 0)
    for tid in ids:
        p = await _call(V3_NPM, S["positions(uint256)"] + _u(tid))
        if len(p) < 2 + 64 * 8:
            continue
        L = _w(p, 7)
        if not L:
            continue
        t0, t1, fee = _addr(p, 2), _addr(p, 3), _w(p, 4)
        tl, tu = _s24(_w(p, 5)), _s24(_w(p, 6))
        pool = _addr(await _call(factory, S["getPool(address,address,uint24)"] + _a(t0) + _a(t1) + _u(fee)), 0)
        sqrt_p = _w(await _call(pool, S["slot0()"]), 0) & ((1 << 160) - 1)
        if not sqrt_p:
            continue
        a0, a1 = _amounts(L, sqrt_p, tl, tu)
        d0, d1 = await decimals(t0), await decimals(t1)
        usd, unpriced = await _value_pair(t0, t1, a0 / 10 ** d0, a1 / 10 ** d1, sqrt_p)
        out.append({"protocol": "Uniswap V3", "kind": "lp", "id": tid, "token0": t0, "token1": t1,
                    "amount0": a0 / 10 ** d0, "amount1": a1 / 10 ** d1, "fee": fee,
                    "usd": round(usd, 2) if usd is not None else None, "unpriced": unpriced})
    return out


async def morpho_blue(wallet: str) -> list[dict]:
    out = []
    for m in await _registry("morpho_market"):
        mid = m["address"]  # market id stored in the address column (bytes32)
        pos = await _call(MORPHO, S["position(bytes32,address)"] + mid[2:].rjust(64, "0") + _a(wallet))
        if len(pos) < 2 + 64 * 3:
            continue
        ss, bs, coll = _w(pos, 0), _w(pos, 1), _w(pos, 2)
        if not (ss or bs or coll):
            continue
        mk = await _call(MORPHO, S["market(bytes32)"] + mid[2:].rjust(64, "0"))
        params = await _call(MORPHO, S["idToMarketParams(bytes32)"] + mid[2:].rjust(64, "0"))
        loan, ctok, oracle = _addr(params, 0), _addr(params, 1), _addr(params, 2)
        tsa, tss, tba, tbs = _w(mk, 0), _w(mk, 1), _w(mk, 2), _w(mk, 3)
        dl, dc = await decimals(loan), await decimals(ctok)
        supplied = (ss * tsa / tss / 10 ** dl) if tss else 0.0
        borrowed = (bs * tba / tbs / 10 ** dl) if tbs else 0.0
        collateral = coll / 10 ** dc
        pl = await price_usd(loan) or 0
        # the market's own oracle: collateral_raw * price / 1e36 = loan_raw
        opx = _w(await _call(oracle, S["price()"]), 0) if int(oracle, 16) else 0
        if opx:
            coll_usd = coll * opx / 10 ** 36 / 10 ** dl * pl
        else:
            coll_usd = collateral * (await price_usd(ctok) or 0)
        ltv = (borrowed * pl / coll_usd) if coll_usd else None
        usd = supplied * pl + coll_usd - borrowed * pl
        out.append({"protocol": "Morpho Blue", "kind": "lending", "market": mid, "loan_token": loan,
                    "collateral_token": ctok, "supplied": supplied, "borrowed": borrowed,
                    "collateral": collateral, "collateral_usd": round(coll_usd, 2),
                    "borrowed_usd": round(borrowed * pl, 2),
                    "ltv": round(ltv, 3) if ltv is not None else None, "usd": round(usd, 2)})
    return out


async def erc4626_vaults(wallet: str) -> list[dict]:
    out = []
    vaults = await _registry("erc4626")
    addrs = [v["address"] for v in vaults]
    bals = await node.balances_of(wallet, addrs)
    for v in vaults:
        sh = int(bals.get(v["address"], 0))
        if not sh:
            continue
        assets = _w(await _call(v["address"], S["convertToAssets(uint256)"] + _u(sh)), 0)
        asset = _addr(await _call(v["address"], S["asset()"]), 0)
        d = await decimals(asset)
        amt = assets / 10 ** d
        px = await price_usd(asset)
        out.append({"protocol": v.get("protocol") or "ERC-4626 vault", "kind": "vault",
                    "vault": v["address"], "asset": asset, "amount": amt,
                    "usd": round(amt * px, 2) if px is not None else None})
    return out


async def _value_pool_token(pool: str, user_bal: int) -> tuple[float | None, dict]:
    """Generic valuation of `user_bal` raw units of a token that may be a pool share.
    A plain token with a market is priced directly; a UniV2-style pool via
    getReserves()+token0/1; any other pool via the pool contract's own balances of
    the tokens the index has seen it hold, times the user's share of totalSupply.
    None when nothing can be priced."""
    px = await price_usd(pool)
    if px is not None:
        d = await decimals(pool)
        return user_bal / 10 ** d * px, {"token": pool, "amount": user_bal / 10 ** d}
    ts = _w(await _call(pool, S["totalSupply()"]), 0)
    if not ts or not user_bal:
        return None, {}
    share = user_bal / ts
    t0r, t1r = await _call(pool, S["token0()"]), await _call(pool, S["token1()"])
    rs = await _call(pool, S["getReserves()"])
    detail = {}
    if t0r not in ("0x", "") and rs not in ("0x", ""):
        t0, t1 = _addr(t0r, 0), _addr(t1r, 0)
        r0, r1 = _w(rs, 0), _w(rs, 1)
        d0, d1 = await decimals(t0), await decimals(t1)
        a0, a1 = r0 / 10 ** d0 * share, r1 / 10 ** d1 * share
        detail = {"token0": t0, "token1": t1, "amount0": a0, "amount1": a1}
        p0, p1 = await price_usd(t0), await price_usd(t1)
        if p0 is None and p1 is not None and a0:
            p0 = (r1 / 10 ** d1) / (r0 / 10 ** d0) * p1
        if p1 is None and p0 is not None and a1:
            p1 = (r0 / 10 ** d0) / (r1 / 10 ** d1) * p0
        return a0 * (p0 or 0) + a1 * (p1 or 0), detail
    # fallback: tokens the pool has ever received, live balances, user's share
    toks = [r["token"] for r in await ch.q(
        f"SELECT token, count() AS n FROM rh.token_transfers WHERE to_addr='{pool}' AND kind='erc20' "
        f"GROUP BY token ORDER BY n DESC LIMIT 4", timeout=30)]
    held = await node.balances_of(pool, toks)
    usd, legs, any_priced = 0.0, {}, False
    for t, raw in held.items():
        d = await decimals(t)
        amt = raw / 10 ** d * share
        px = await price_usd(t)
        legs[t] = amt
        if px is not None and amt:
            any_priced = True
            usd += amt * px
    if any_priced:
        return usd, {"legs": legs}
    return await _value_cow_lp(pool, user_bal, ts)


async def _value_cow_lp(lp: str, user_bal: int, total_supply_raw: int) -> tuple[float | None, dict]:
    """cow-family LP (cowUniswap… / cowUp33… / cowUniswapV4…): the LP contract holds
    nothing, so the registry recorded where its reserves live:
      v4 — the manager's Uniswap V4 position: live liquidity via PoolManager.extsload
           (positions mapping), amounts by tick math, the user's share of them.
      cp — constant-product pool: value per LP = 2·sqrt(k_per_LP² · Px · Py), with k
           taken from the deposit ratio of a mint (fees since then make it a floor)."""
    row = await ch.one(f"SELECT meta FROM rh.protocol_registry WHERE kind='cow_lp' AND address='{lp}'")
    if not row or not row.get("meta"):
        return None, {}
    m = json.loads(row["meta"])
    share = user_bal / total_supply_raw
    if m["mode"] == "v4":
        pid = bytes.fromhex(m["pool"][2:])
        base = int.from_bytes(keccak(pid + (6).to_bytes(32, "big")), "big")
        raw0 = _w(await _call(V4_PM, S["extsload(bytes32)"] + _u(base)), 0)
        sqrt_p = raw0 & ((1 << 160) - 1)
        pk = await ch.one(f"SELECT token0, token1 FROM rh.dex_pools WHERE pool='{m['pool']}'")
        if not pk or not sqrt_p:
            return None, {"method": "cow v4 position", "note": "pool not in dex_pools"}
        c0, c1 = pk["token0"], pk["token1"]
        a0 = a1 = 0
        live_ranges = 0
        for rg in m.get("ranges", []):
            tl, tu = rg["tl"], rg["tu"]
            key = keccak(bytes.fromhex(m["owner"][2:]) + (tl & 0xFFFFFF).to_bytes(3, "big")
                         + (tu & 0xFFFFFF).to_bytes(3, "big") + bytes.fromhex(rg["salt"][2:]))
            pslot = int.from_bytes(keccak(key + ((base + 6) % 2 ** 256).to_bytes(32, "big")), "big")
            L = _w(await _call(V4_PM, S["extsload(bytes32)"] + _u(pslot)), 0) & ((1 << 128) - 1)
            if not L:
                continue
            live_ranges += 1
            x0, x1 = _amounts(L, sqrt_p, tl, tu)
            a0, a1 = a0 + x0, a1 + x1
        if not live_ranges:
            return None, {"method": "cow v4 position", "note": "no live liquidity found"}
        d0, d1 = await decimals(c0), await decimals(c1)
        usd, unpriced = await _value_pair(c0, c1, a0 / 10 ** d0 * share, a1 / 10 ** d1 * share, sqrt_p)
        return usd, {"method": "cow v4 position", "token0": c0, "token1": c1,
                     "amount0": a0 / 10 ** d0 * share, "amount1": a1 / 10 ** d1 * share, "unpriced": unpriced}
    if m["mode"] == "v3":
        pool = m["pool"]
        sqrt_p = _w(await _call(pool, S["slot0()"]), 0) & ((1 << 160) - 1)
        t0 = _addr(await _call(pool, S["token0()"]), 0)
        t1 = _addr(await _call(pool, S["token1()"]), 0)
        if not sqrt_p or not int(t0, 16):
            return None, {"method": "cow v3 position", "note": "pool unreadable"}
        a0 = a1 = 0
        live = 0
        for rg in m.get("ranges", []):
            tl, tu = rg["tl"], rg["tu"]
            key = keccak(bytes.fromhex(m["owner"][2:]) + (tl & 0xFFFFFF).to_bytes(3, "big") + (tu & 0xFFFFFF).to_bytes(3, "big"))
            r = await _call(pool, S["positions(bytes32)"] + key.hex())
            L = _w(r, 0) & ((1 << 128) - 1) if r not in ("0x", "") else 0
            if not L:
                continue
            live += 1
            x0, x1 = _amounts(L, sqrt_p, tl, tu)
            a0, a1 = a0 + x0, a1 + x1
        if not live:
            return None, {"method": "cow v3 position", "note": "no live liquidity found"}
        d0, d1 = await decimals(t0), await decimals(t1)
        usd, unpriced = await _value_pair(t0, t1, a0 / 10 ** d0 * share, a1 / 10 ** d1 * share, sqrt_p)
        return usd, {"method": "cow v3 position", "token0": t0, "token1": t1,
                     "amount0": a0 / 10 ** d0 * share, "amount1": a1 / 10 ** d1 * share, "unpriced": unpriced}
    if m["mode"] == "cp":
        x, y = m["x"], m["y"]
        dx, dy = await decimals(x), await decimals(y)
        px, py = await price_usd(x), await price_usd(y)
        if px is None or py is None:
            return None, {"method": "cow constant-product estimate", "unpriced": [t for t, p in ((x, px), (y, py)) if p is None]}
        k_per_lp = (m["x0"] / 10 ** dx) * (m["y0"] / 10 ** dy) / (m["l0"] ** 2)   # k / L² in token units
        val_per_lp = 2 * math.sqrt(k_per_lp * px * py)
        return val_per_lp * user_bal, {"method": "cow constant-product estimate (floor)", "token0": x, "token1": y}
    return None, {}


async def staking_and_pools(wallet: str) -> list[dict]:
    out = []
    gauges = await _registry("gauge")
    pairs = await _registry("pool_token")
    addrs = [g["address"] for g in gauges] + [p["address"] for p in pairs]
    bals = await node.balances_of(wallet, addrs)
    lp_of = {g["address"]: (g.get("meta") or "") for g in gauges}
    for g in gauges:
        b = int(bals.get(g["address"], 0))
        if not b:
            continue
        lp = lp_of.get(g["address"]) or ""
        usd, detail = (await _value_pool_token(lp, b)) if lp.startswith("0x") else (None, {})
        sd = await decimals(lp) if lp.startswith("0x") else 18
        out.append({"protocol": g.get("protocol") or "staking", "kind": "staked",
                    "gauge": g["address"], "staked_token": lp or None, "staked": b / 10 ** sd,
                    "usd": round(usd, 2) if usd is not None else None, **detail})
    for p in pairs:
        b = int(bals.get(p["address"], 0))
        if not b:
            continue
        usd, detail = await _value_pool_token(p["address"], b)
        out.append({"protocol": p.get("protocol") or "pool", "kind": "lp_token",
                    "pool": p["address"], "balance": b / 10 ** (await decimals(p["address"])),
                    "usd": round(usd, 2) if usd is not None else None, **detail})
    return out


async def lockers(wallet: str) -> list[dict]:
    """Token lockers / vesting escrows (UNCX-style `Locked` emitters). They share no
    ABI, so the position is read from the ledger itself: what the wallet sent INTO
    the locker minus what the locker sent back, per token. Priced like any holding."""
    out = []
    regs = await _registry("locker")
    if not regs:
        return out
    addrs = "','".join(r["address"] for r in regs)
    try:
        rows = await ch.q(f"SELECT locker, token, net FROM rh.locker_positions WHERE wallet='{wallet}' AND net > 0", timeout=10)
    except Exception:
        rows = None
    if rows is None:
        rows = await ch.q(f"""
        SELECT locker, token, sumIf(v, dir='in') - sumIf(v, dir='out') AS net FROM (
            SELECT to_addr AS locker, token, toFloat64(value) AS v, 'in' AS dir FROM rh.token_transfers
            WHERE from_addr='{wallet}' AND to_addr IN ('{addrs}') AND kind='erc20'
            UNION ALL
            SELECT from_addr AS locker, token, toFloat64(value) AS v, 'out' AS dir FROM rh.token_transfers
            WHERE to_addr='{wallet}' AND from_addr IN ('{addrs}') AND kind='erc20')
        GROUP BY locker, token HAVING net > 0""", timeout=60)  # noqa: E501
    proto = {r["address"]: r.get("protocol") or "locker" for r in regs}
    for r in rows:
        t = r["token"]
        d = await decimals(t)
        amt = float(r["net"]) / 10 ** d
        px = await price_usd(t)
        if px is None:
            usd, detail = await _value_pool_token(t, int(float(r["net"])))
            usd = usd if usd is not None else None
        else:
            usd, detail = amt * px, {}
        out.append({"protocol": proto.get(r["locker"], "locker"), "kind": "locked", "locker": r["locker"],
                    "token": t, "amount": amt, "usd": round(usd, 2) if usd is not None else None, **detail})
    return out


async def lighter(wallet: str) -> list[dict]:
    if wallet == ZERO:
        return []
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"{LIGHTER_BASE}/api/v1/account",
                            params={"by": "l1_address", "value": wallet})
        if r.status_code != 200:
            return []
        accts = [a for a in ((r.json() or {}).get("accounts") or [])
                 if str(a.get("l1_address", wallet)).lower() == wallet]
        if not accts:
            return []
        a = accts[0]
        total = float(a.get("total_asset_value") or a.get("collateral") or 0)
        return [{"protocol": "Lighter", "kind": "perp_account",
                 "account_index": a.get("account_index"),
                 "collateral": float(a.get("collateral") or 0),
                 "available": float(a.get("available_balance") or 0),
                 "usd": round(total, 2)}]
    except Exception:
        return []


async def wallet_positions(wallet: str) -> dict:
    """All protocol positions for a wallet, priced, with a per-protocol rollup."""
    w = ch.addr(wallet)
    if w in (ZERO, DEAD):
        return {"wallet": w, "protocol_usd": 0.0, "by_protocol": [], "positions": [],
                "unpriced_positions": 0, "errors": ["burn address — not a wallet"]}
    _px_cache.clear()
    _px_source.clear()
    results = await asyncio.gather(
        uniswap_v4(w), uniswap_v3(w), morpho_blue(w), erc4626_vaults(w),
        staking_and_pools(w), lockers(w), lighter(w), return_exceptions=True)
    positions: list[dict] = []
    errors: list[str] = []
    for name, r in zip(("uniswap_v4", "uniswap_v3", "morpho", "vaults", "staking", "lockers", "lighter"), results):
        if isinstance(r, Exception):
            errors.append(f"{name}: {str(r)[:80]}")
        else:
            positions.extend(r)
    by: dict[str, float] = {}
    unpriced = 0
    for p in positions:
        if p.get("usd") is None:
            unpriced += 1
        by[p["protocol"]] = by.get(p["protocol"], 0.0) + float(p.get("usd") or 0)
    total = sum(by.values())
    return {
        "wallet": w,
        "protocol_usd": round(total, 2),
        "unpriced_positions": unpriced,
        "by_protocol": [{"protocol": k, "usd": round(v, 2)} for k, v in
                        sorted(by.items(), key=lambda kv: kv[1], reverse=True)],
        "positions": positions,
        "price_sources": {t: src for t, src in _px_source.items() if src not in ("index",)},
        "errors": errors,
    }
