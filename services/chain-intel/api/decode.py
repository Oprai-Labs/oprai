"""Real-time trade decoding and sellability simulation — the two reads a copy-trader
needs BEFORE the next block, so both go to the node directly (never the index):

  decode_tx(hash)      one receipt → every swap leg (venue, pool / PoolKey, tokens,
                       amounts, price) + the sender's NET result (what was bought /
                       sold, quote spent / received, direction). Sub-second.
  simulate_sell(token) can this token be sold right now, and at what cost? Quotes a
                       small sell AND buy through the venue's own quoter (Uniswap
                       V4Quoter / V3 QuoterV2) or the Pons curve, compares to spot.

Venue semantics handled here once (they differ):
  V3 Swap amounts are POOL deltas (positive = pool received),
  V4 Swap amounts are SWAPPER deltas (negative = swapper paid),
  V2 Swap carries explicit in/out, Pons curve events carry (quoteIn, tokensOut) /
  (tokensIn, quoteOut)."""
from __future__ import annotations

import asyncio
import math
import os

import httpx
from eth_hash.auto import keccak

from . import ch, node, positions as P
from .reports import _HOOK_LAUNCHPAD, _LAUNCHPAD_ROUTERS, _PLATFORM_CONTRACTS

NODE = os.environ.get("NODE_RPC", "http://rh-nitro:8547")
V4_PM = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
V4_QUOTER = "0x8dc178efb8111bb0973dd9d722ebeff267c98f94"    # developers.uniswap.org v4 deployments (4663)
V3_QUOTER2 = "0x33e885ed0ec9bf04ecfb19341582aadcb4c8a9e7"   # developers.uniswap.org v3 deployments (4663)
V3_FACTORY = "0x1f7d7550b1b028f7571e69a784071f0205fd2efa"
ZERO, WETH, USDG, USDE = P.ZERO, P.WETH, P.USDG, P.USDE
QUOTES = {ZERO, WETH, USDG, USDE}
Q96 = 2 ** 96

T_V4_SWAP = "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"
T_V3_SWAP = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
T_V2_SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
T_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
T_WETH_WITHDRAW = "0x7fcf532c15f0a6db0bd6d0e038bea71d30d808c7d98cb3bf7268a95bf5081b65"
T_PONS_BUY = "0xec36bf571f136799e8dc0b0b8bea4b04d8bd3d43de838aab0d5fc21d4cbfc455"
T_PONS_SELL = "0x8113d738abdcb6b38357e9d53a54a7157861a09031b453651f0fe7fe151f59df"
T_USEROP = "0x49628fd1471006c1482da88028e9ce4dbb080b815c9b0344d39e5a8e6ec1419f"   # ERC-4337 UserOperationEvent(hash, sender, paymaster, nonce, success, ...)
ENTRYPOINTS = {"0x0000000071727de22e5e9d8baf0edac6f37da032", "0x5ff137d4b0fdcd49dca30c7cf57e578a026d2789"}


def _sel(sig: str) -> str:
    return "0x" + keccak(sig.encode()).hex()[:8]


def _s(v: int, bits: int) -> int:
    return v - (1 << bits) if v >= (1 << (bits - 1)) else v


def _w(hx: str, i: int) -> int:
    hx = hx[2:] if hx.startswith("0x") else hx
    s = hx[i * 64:(i + 1) * 64]
    return int(s, 16) if s else 0


def _u(n: int) -> str:
    return hex(n & (2 ** 256 - 1))[2:].rjust(64, "0")


def _a(addr: str) -> str:
    return addr.lower().replace("0x", "").rjust(64, "0")


async def rpc(method: str, params: list, timeout: float = 8.0):
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(NODE, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        r.raise_for_status()
        j = r.json()
        if "error" in j:
            raise RuntimeError(j["error"].get("message", "rpc error"))
        return j["result"]


async def _quote_usd(token: str, amt: float) -> float | None:
    if token in (USDG,):
        return amt
    if token in (USDE,):
        return amt
    if token in (ZERO, WETH):
        px = await P.eth_usd()
        return amt * px if px else None
    px = await P.price_usd(token)
    return amt * px if px is not None else None


# ── tx decoding ────────────────────────────────────────────────────────────
async def decode_tx(tx_hash: str) -> dict:
    rec, tx = await asyncio.gather(rpc("eth_getTransactionReceipt", [tx_hash]),
                                   rpc("eth_getTransactionByHash", [tx_hash]))
    if not rec:
        return {"tx": tx_hash, "status": "pending_or_unknown"}
    sender = (tx.get("from") or "").lower()
    callee = (tx.get("to") or "").lower()
    eth_in = int(tx.get("value", "0x0"), 16) / 1e18
    ok = int(rec.get("status", "0x0"), 16) == 1
    legs: list[dict] = []
    net_all: dict[str, dict[str, int]] = {}   # address → token → net raw change
    eth_out = 0.0
    P._px_cache.clear()
    userop_sender = None

    for lg in rec.get("logs", []):
        topics = lg.get("topics") or []
        if not topics:
            continue
        t0, addr = topics[0].lower(), lg["address"].lower()
        data = lg.get("data", "0x")
        if t0 == T_TRANSFER and len(topics) == 3:
            frm, to = "0x" + topics[1][-40:], "0x" + topics[2][-40:]
            v = _w(data, 0)
            net_all.setdefault(to, {})[addr] = net_all.setdefault(to, {}).get(addr, 0) + v
            net_all.setdefault(frm, {})[addr] = net_all.setdefault(frm, {}).get(addr, 0) - v
        elif t0 == T_USEROP and len(topics) >= 3:
            userop_sender = "0x" + topics[2][-40:]
        elif t0 == T_WETH_WITHDRAW and addr == WETH:
            eth_out += _w(data, 0) / 1e18
        elif t0 == T_V4_SWAP:
            pid = topics[1].lower()
            key = await ch.one(f"SELECT token0, token1, fee, tick_spacing, hooks FROM rh.v4_pool_keys WHERE pool='{pid}'")
            a0, a1 = _s(_w(data, 0), 256), _s(_w(data, 1), 256)   # swapper deltas (int128, sign-extended)
            if key:
                tin, tout = (key["token0"], key["token1"]) if a0 < 0 else (key["token1"], key["token0"])
                ain, aout = (-a0, a1) if a0 < 0 else (-a1, a0)
                legs.append({"venue": "Uniswap V4", "pool": pid, "pool_key": {
                    "currency0": key["token0"], "currency1": key["token1"], "fee": int(key["fee"]),
                    "tick_spacing": int(key["tick_spacing"]), "hooks": key["hooks"]},
                    "hook_name": _HOOK_LAUNCHPAD.get(key["hooks"]), "token_in": tin, "token_out": tout,
                    "amount_in_raw": ain, "amount_out_raw": aout, "sqrt_price_x96": _w(data, 2),
                    "fee_pips": _w(data, 5)})
            else:
                legs.append({"venue": "Uniswap V4", "pool": pid, "note": "pool key not indexed yet"})
        elif t0 == T_V3_SWAP:
            pool = await ch.one(f"SELECT token0, token1, fee FROM rh.dex_pools WHERE pool='{addr}'")
            a0, a1 = _s(_w(data, 0), 256), _s(_w(data, 1), 256)   # pool deltas
            if pool:
                tin, tout = (pool["token0"], pool["token1"]) if a0 > 0 else (pool["token1"], pool["token0"])
                ain, aout = (a0, -a1) if a0 > 0 else (a1, -a0)
                legs.append({"venue": "Uniswap V3", "pool": addr, "fee": int(pool["fee"] or 0),
                             "token_in": tin, "token_out": tout, "amount_in_raw": ain, "amount_out_raw": aout,
                             "sqrt_price_x96": _w(data, 2)})
        elif t0 == T_V2_SWAP:
            pool = await ch.one(f"SELECT token0, token1 FROM rh.dex_pools WHERE pool='{addr}'")
            i0, i1, o0, o1 = _w(data, 0), _w(data, 1), _w(data, 2), _w(data, 3)
            if pool:
                tin, tout = (pool["token0"], pool["token1"]) if i0 > 0 else (pool["token1"], pool["token0"])
                legs.append({"venue": "Uniswap V2", "pool": addr, "token_in": tin, "token_out": tout,
                             "amount_in_raw": i0 or i1, "amount_out_raw": o1 if i0 > 0 else o0})
        elif t0 in (T_PONS_BUY, T_PONS_SELL):
            cv = await ch.one(f"SELECT token, quote FROM rh.pons_curves WHERE curve='{addr}'")
            if cv:
                buy = t0 == T_PONS_BUY
                legs.append({"venue": "Pons curve", "pool": addr, "curve": addr, "token": cv["token"],
                             "quote": cv["quote"], "token_in": cv["quote"] if buy else cv["token"],
                             "token_out": cv["token"] if buy else cv["quote"],
                             "amount_in_raw": _w(data, 0), "amount_out_raw": _w(data, 1),
                             "fee_raw": _w(data, 2), "tax_raw": _w(data, 3)})

    # who acted: the tx sender, unless this is a bundled / relayed tx — then the address
    # that ended up holding the swapped (non-quote) token. Pools, routers and the
    # PoolManager are never the actor.
    infra = {l.get("pool") for l in legs} | {V4_PM, WETH, ZERO} | ENTRYPOINTS | set(_PLATFORM_CONTRACTS) | set(_LAUNCHPAD_ROUTERS)
    actor = sender
    if userop_sender:
        actor = userop_sender
    elif not any(v for t, v in net_all.get(sender, {}).items() if t not in QUOTES):
        cands = [(sum(abs(v) for t, v in m.items() if t not in QUOTES), a) for a, m in net_all.items() if a not in infra]
        if cands:
            actor = max(cands)[1]
    net = net_all.get(actor, {})
    if actor != sender:
        eth_in = 0.0   # value was the bundler's; the actor's quote spend is in its ERC20 net
    # decimals + human amounts + per-leg price
    toks = {x for l in legs for x in (l.get("token_in"), l.get("token_out")) if x} | set(net)
    dec = {t: await P.decimals(t) for t in toks}
    for l in legs:
        if "amount_in_raw" in l and l.get("token_in"):
            l["amount_in"] = l["amount_in_raw"] / 10 ** dec[l["token_in"]]
            l["amount_out"] = l["amount_out_raw"] / 10 ** dec[l["token_out"]]
            if l["amount_out"]:
                l["price_in_per_out"] = l["amount_in"] / l["amount_out"]

    # sender's net result
    bought = [{"token": t, "amount": v / 10 ** dec[t]} for t, v in net.items() if v > 0 and t not in QUOTES]
    sold = [{"token": t, "amount": -v / 10 ** dec[t]} for t, v in net.items() if v < 0 and t not in QUOTES]
    quote_spent = {ZERO: eth_in} if eth_in else {}
    quote_recv = {ZERO: eth_out} if eth_out else {}
    for t, v in net.items():
        if t in QUOTES and v < 0:
            quote_spent[t] = quote_spent.get(t, 0) + (-v) / 10 ** dec[t]
        if t in QUOTES and v > 0:
            quote_recv[t] = quote_recv.get(t, 0) + v / 10 ** dec[t]
    spent_usd = recv_usd = 0.0
    for t, a in quote_spent.items():
        spent_usd += (await _quote_usd(t, a)) or 0
    for t, a in quote_recv.items():
        recv_usd += (await _quote_usd(t, a)) or 0
    direction = "buy" if bought and not sold else "sell" if sold and not bought else "swap" if (bought and sold) else "other"
    # Relayed / aggregator paths hide the actor's quote spend from the transfer net
    # (the router wrapped ETH, or a bundler paid). Fall back to the swap legs: quote
    # entering the route that no earlier leg produced is what the trade cost.
    if direction == "buy" and not spent_usd:
        produced = {l.get("token_out") for l in legs}
        for l in legs:
            if l.get("token_in") in QUOTES and l.get("amount_in") and l["token_in"] not in produced:
                spent_usd += (await _quote_usd(l["token_in"], l["amount_in"])) or 0
                quote_spent[l["token_in"]] = quote_spent.get(l["token_in"], 0) + l["amount_in"]
    if direction == "sell" and not recv_usd:
        consumed = {l.get("token_in") for l in legs}
        for l in legs:
            if l.get("token_out") in QUOTES and l.get("amount_out") and l["token_out"] not in consumed:
                recv_usd += (await _quote_usd(l["token_out"], l["amount_out"])) or 0
                quote_recv[l["token_out"]] = quote_recv.get(l["token_out"], 0) + l["amount_out"]
    for b in bought:
        b["symbol"] = None
        if direction == "buy" and spent_usd and b["amount"]:
            b["price_usd"] = spent_usd / b["amount"]
            b["cost_usd"] = spent_usd
    for s_ in sold:
        if direction == "sell" and recv_usd and s_["amount"]:
            s_["price_usd"] = recv_usd / s_["amount"]
            s_["proceeds_usd"] = recv_usd
    syms = await node.resolve_symbols([b["token"] for b in bought + sold])
    for x in bought + sold:
        x["symbol"] = syms.get(x["token"])
    launch = next((l.get("hook_name") for l in legs if l.get("hook_name")), None) \
        or ("Pons" if any(l["venue"] == "Pons curve" for l in legs) else None)
    return {
        "tx": tx_hash, "ok": ok, "block": int(rec.get("blockNumber", "0x0"), 16),
        "from": sender, "actor": actor, "relayed": actor != sender, "to": callee,
        "to_label": _PLATFORM_CONTRACTS.get(callee) or _LAUNCHPAD_ROUTERS.get(callee),
        "direction": direction, "bought": bought, "sold": sold,
        "quote_spent": quote_spent, "quote_received": quote_recv,
        "spent_usd": round(spent_usd, 2), "received_usd": round(recv_usd, 2),
        "venues": sorted({l["venue"] for l in legs}), "launchpad": launch, "legs": legs,
    }


# ── sellability simulation ─────────────────────────────────────────────────
def _enc_v4_single(key: dict, zero_for_one: bool, amount: int) -> str:
    """quoteExactInputSingle(((c0,c1,fee,tickSpacing,hooks),zeroForOne,uint128 exactAmount,bytes hookData))"""
    body = (_a(key["currency0"]) + _a(key["currency1"]) + _u(key["fee"]) + _u(key["tick_spacing"]) + _a(key["hooks"])
            + _u(1 if zero_for_one else 0) + _u(amount) + _u(0x100) + _u(0))
    return _sel("quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))") + _u(0x20) + body


async def _v4_quote(key: dict, zero_for_one: bool, amount: int) -> int | None:
    try:
        r = await node.eth_call(V4_QUOTER, _enc_v4_single(key, zero_for_one, amount))
        return _w(r, 0) if r not in ("0x", "") else None
    except Exception:
        return None


async def _v3_quote(tin: str, tout: str, fee: int, amount: int) -> int | None:
    data = (_sel("quoteExactInputSingle((address,address,uint256,uint24,uint160))")
            + _a(tin) + _a(tout) + _u(amount) + _u(fee) + _u(0))
    try:
        r = await node.eth_call(V3_QUOTER2, data)
        return _w(r, 0) if r not in ("0x", "") else None
    except Exception:
        return None


async def _candidate_pools(t: str, limit: int = 8) -> list[dict]:
    """The token's USDG/WETH/ETH pools ranked by swaps in the last ~2 days (the live
    market), falling back to any such pool when it has not traded recently."""
    quotes = "','".join(QUOTES)
    hi = int(await ch.scalar("SELECT max(block_number) FROM rh.dex_swaps") or 0)
    active = await ch.q(f"""
        SELECT pool, count() AS n FROM rh.dex_swaps
        WHERE (token_in='{t}' OR token_out='{t}') AND block_number > {hi - 1_800_000}
          AND (token_in IN ('{quotes}') OR token_out IN ('{quotes}'))
        GROUP BY pool ORDER BY n DESC LIMIT {limit}""", timeout=15)
    pools = [r["pool"] for r in active]
    if pools:
        pin = "','".join(pools)
        rows = await ch.q(f"SELECT pool, token0, token1, dex, fee FROM rh.dex_pools WHERE pool IN ('{pin}')")
        order = {p: i for i, p in enumerate(pools)}
        return sorted(rows, key=lambda r: order.get(r["pool"], 99))
    return await ch.q(f"SELECT pool, token0, token1, dex, fee FROM rh.dex_pools WHERE "
                      f"(token0='{t}' AND token1 IN ('{quotes}')) OR (token1='{t}' AND token0 IN ('{quotes}')) LIMIT {limit}")


async def _best_pool(t: str) -> dict | None:
    """The token's deepest live USDG/WETH/ETH pool with everything a quote needs."""
    rows = await _candidate_pools(t)
    best = None
    for r in rows:
        if r["dex"] == "uniswap-v4":
            k = await ch.one(f"SELECT token0, token1, fee, tick_spacing, hooks FROM rh.v4_pool_keys WHERE pool='{r['pool']}'")
            if not k:
                continue
            base = int.from_bytes(keccak(bytes.fromhex(r["pool"][2:]) + (6).to_bytes(32, "big")), "big")
            raw = _w(await P._call(V4_PM, P.S["extsload(bytes32)"] + _u(base)), 0)
            liq = _w(await P._call(V4_PM, P.S["extsload(bytes32)"] + _u((base + 3) % 2 ** 256)), 0)
            sqrt_p, lp_fee = raw & ((1 << 160) - 1), (raw >> 208) & 0xFFFFFF
            cand = {"dex": "v4", "pool": r["pool"], "key": {"currency0": k["token0"], "currency1": k["token1"],
                    "fee": int(k["fee"]), "tick_spacing": int(k["tick_spacing"]), "hooks": k["hooks"]},
                    "token0": k["token0"], "token1": k["token1"], "sqrt_p": sqrt_p, "liq": liq, "lp_fee": lp_fee,
                    "dynamic_fee": int(k["fee"]) == 0x800000}
        elif r["dex"] == "uniswap-v3":
            raw = _w(await P._call(r["pool"], P.S["slot0()"]), 0)
            liq = _w(await P._call(r["pool"], P.S["liquidity()"]), 0)
            cand = {"dex": "v3", "pool": r["pool"], "fee": int(r["fee"] or 0), "token0": r["token0"], "token1": r["token1"],
                    "sqrt_p": raw & ((1 << 160) - 1), "liq": liq, "lp_fee": int(r["fee"] or 0), "dynamic_fee": False}
        else:
            continue
        if not cand["sqrt_p"] or not cand["liq"]:
            continue
        if best is None or cand["liq"] > best["liq"]:
            best = cand
    return best


async def simulate_sell(token: str, usd: float = 10.0) -> dict:
    """Quote a $usd sell AND buy of the token through its deepest pool's own quoter;
    compare to the pool's spot price. cost_pct = what you lose vs spot (LP fee +
    hook tax + impact on a small size). A quoter revert = the pool will not let you
    out right now."""
    t = ch.addr(token)
    out: dict = {"token": t, "sim_usd": usd}
    # still on a Pons curve → the curve itself is the venue
    cv = await ch.one(f"SELECT curve, quote FROM rh.pons_curves WHERE token='{t}'")
    if cv:
        st = await node.pons_curve_state(t)
        if st and not st.get("graduated"):
            out.update({"venue": "Pons curve", "pool": cv["curve"], "can_sell": bool(st.get("token_reserve")),
                        "sell_cost_pct": round((st.get("fee_bps", 0) + st.get("tax_bps", 0)) / 100, 2),
                        "buy_cost_pct": round(st.get("fee_bps", 0) / 100, 2), "curve": st})
            return out
    pool = await _best_pool(t)
    if not pool:
        out.update({"venue": None, "can_sell": None, "note": "no live USDG/WETH pool and no curve — nothing to quote against"})
        return out
    is0 = pool["token0"] == t
    quote = pool["token1"] if is0 else pool["token0"]
    d_t, d_q = await P.decimals(t), await P.decimals(quote)
    p_t_in_q = P._pool_price_from_sqrt(pool["sqrt_p"], d_t if is0 else d_q, d_q if is0 else d_t)
    if not is0:
        p_t_in_q = 1 / p_t_in_q if p_t_in_q else 0
    q_usd = await _quote_usd(quote, 1.0) or 0
    spot_usd = p_t_in_q * q_usd
    out.update({"venue": "Uniswap V4" if pool["dex"] == "v4" else "Uniswap V3", "pool": pool["pool"],
                "quote_token": quote, "spot_price_usd": spot_usd, "lp_fee_pct": round(pool["lp_fee"] / 10000, 4),
                "dynamic_fee": pool["dynamic_fee"], "hook": (pool.get("key") or {}).get("hooks"),
                "hook_name": _HOOK_LAUNCHPAD.get((pool.get("key") or {}).get("hooks", "")),})
    if not spot_usd:
        out.update({"can_sell": None, "note": "spot price unknown"})
        return out
    async def q_sell(u: float) -> int | None:
        amt = int(u / spot_usd * 10 ** d_t)
        return (await _v4_quote(pool["key"], is0, amt)) if pool["dex"] == "v4" else (await _v3_quote(t, quote, pool["fee"], amt))

    async def q_buy(u: float) -> int | None:
        amt = int(u / q_usd * 10 ** d_q) if q_usd else 0
        if not amt:
            return None
        return (await _v4_quote(pool["key"], not is0, amt)) if pool["dex"] == "v4" else (await _v3_quote(quote, t, pool["fee"], amt))

    # a revert at $usd can be thin liquidity, not a block — step the size down before
    # calling it unsellable; report the size that actually filled
    sell_out, sell_size = None, None
    for u in (usd, usd / 10, usd / 100):
        sell_out = await q_sell(u)
        if sell_out:
            sell_size = u
            break
    out["can_sell"] = bool(sell_out)
    if sell_out:
        got_usd = sell_out / 10 ** d_q * q_usd
        out["sell_size_usd"] = sell_size
        out["sell_cost_pct"] = round(max(0.0, (1 - got_usd / sell_size)) * 100, 2)
        out["sell_receives_usd"] = round(got_usd, 4)
        if sell_size < usd:
            out["note"] = f"a ${usd:g} sell reverts; only ~${sell_size:g} fills — liquidity is very thin"
    else:
        out["note"] = "sell quote reverts even at tiny size — the pool/hook refuses sells right now (honeypot-like or paused)"
    buy_out = await q_buy(usd)
    out["can_buy"] = bool(buy_out)
    if buy_out:
        got_tokens_usd = buy_out / 10 ** d_t * spot_usd
        out["buy_cost_pct"] = round(max(0.0, (1 - got_tokens_usd / usd)) * 100, 2)
    if out.get("can_sell") and out.get("sell_cost_pct") is not None:
        extra = out["sell_cost_pct"] - out["lp_fee_pct"]
        out["hook_tax_pct_est"] = round(max(0.0, extra), 2) if (out.get("hook") and extra > 0.5) else 0.0
    return out
