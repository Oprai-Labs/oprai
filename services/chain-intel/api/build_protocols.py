"""Build `protocol_registry` from on-chain EVENTS — the contract sets the positions
adapters iterate. No hand-written lists: a vault is anything that emits ERC-4626
Deposit, a gauge anything that emits Staked, a locker anything that emits Locked,
a Morpho market anything Morpho Blue announced with CreateMarket, a pool token any
Ramses/GIGA factory pair. Gauge→LP mapping comes from the index (the ERC20 users
send INTO the gauge). Run after build_dex_pools; idempotent (TRUNCATE+INSERT).

  docker exec -w /app rh-chain-intel-api python3 -m api.build_protocols [--since=N]"""
from __future__ import annotations

import asyncio
import json
import sys

import httpx
from eth_hash.auto import keccak

from . import ch, node
from .ch import CH_URL, CH_USER, CH_PASS, CH_DB

MORPHO = "0x9d53d5e3bd5e8d4cbfa6db1ca238aea02e651010"
RAMSES_FACTORY = "0x6fdf38f92ead1adfc04b73aaa947ab254f6c0916"
V4_PM = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
ZERO = "0x0000000000000000000000000000000000000000"
KNOWN_VAULTS = {  # curators we can name (symbol-verified on-chain)
    "0xbeeff033f34c046626b8d0a041844c5d1a5409dd": "Steakhouse (steakUSDG)",
    "0xde770c84fe66e063336b31737cfe9790f18c4087": "Spark (spUSDG)",
}


def topic(sig: str) -> str:
    return "0x" + keccak(sig.encode()).hex()


def sel(sig: str) -> str:
    return "0x" + keccak(sig.encode()).hex()[:8]


async def ch_write(sql: str) -> None:
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(CH_URL, params={"user": CH_USER, "password": CH_PASS, "database": CH_DB},
                         content=sql.encode())
        r.raise_for_status()


async def emitters(t: str, lo: int, hi: int, limit: int = 400) -> list[str]:
    rows = await ch.q(f"SELECT address, count() AS n FROM rh.logs WHERE topic0='{t}' "
                      f"AND block_number BETWEEN {lo} AND {hi} GROUP BY address "
                      f"ORDER BY n DESC LIMIT {limit}", timeout=600)
    return [r["address"] for r in rows]


async def main() -> None:
    since = 20_000_000
    for a in sys.argv[1:]:
        if a.startswith("--since="):
            since = int(a.split("=", 1)[1])
    hi = int(await ch.scalar("SELECT max(block_number) FROM rh.logs") or 0)
    lo = max(0, hi - since)
    print(f"scan {lo}..{hi}", flush=True)
    await ch_write("""CREATE TABLE IF NOT EXISTS rh.protocol_registry (
        address String, kind String, protocol String, meta String, created_at DateTime DEFAULT now()
    ) ENGINE = MergeTree ORDER BY (kind, address)""")

    rows: list[tuple] = []
    # ERC-4626 vaults
    vaults = await emitters(topic("Deposit(address,address,uint256,uint256)"), lo, hi)
    syms = await node.resolve_symbols(vaults)
    for v in vaults:
        rows.append((v, "erc4626", KNOWN_VAULTS.get(v) or f"vault {syms.get(v, '')}".strip(), syms.get(v, "")))
    print(f"  erc4626 vaults: {len(vaults)}", flush=True)

    # staking gauges (+ the LP token users stake into each, from the index)
    gauges = await emitters(topic("Staked(address,uint256)"), lo, hi)
    gsyms = await node.resolve_symbols(gauges)
    for g in gauges:
        lp = await ch.one(f"SELECT token, count() AS n FROM rh.token_transfers WHERE to_addr='{g}' "
                          f"AND kind='erc20' GROUP BY token ORDER BY n DESC LIMIT 1", timeout=60)
        lp_addr = (lp or {}).get("token") or ""
        label = gsyms.get(g, "")
        proto = "Ramses/GIGA gauge" if label.startswith("rcow") or "GIGA" in label else "staking"
        rows.append((g, "gauge", proto, lp_addr))
    lp_of_gauge = {r[0]: r[3] for r in rows if r[1] == "gauge"}
    staked_syms = await node.resolve_symbols([v for v in lp_of_gauge.values() if v])
    rows = [(a, k, ("UP (cow AMM)" if "up33" in staked_syms.get(m, "").lower() else
                    "CoW AMM (Uniswap)" if staked_syms.get(m, "").lower().startswith("cow") else pr), m)
            if k == "gauge" else (a, k, pr, m) for a, k, pr, m in rows]
    print(f"  gauges: {len(gauges)}", flush=True)

    # cow-family LP tokens (cowUniswap… / cowUp33… / cowUniswapV4…): the LP contract
    # holds nothing — find, from a mint tx, where the reserves live:
    #   v4: a ModifyLiquidity in the mint tx → (poolId, owner, tickLower, tickUpper, salt)
    #   cp: both named tokens moved in → constant-product k per LP² from (x0, y0, L0)
    ML = topic("ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)")
    lp_syms = await node.resolve_symbols([r[3] for r in rows if r[1] == "gauge" and r[3]])
    cow_lps = sorted({a for a, sy in lp_syms.items() if sy.lower().startswith("cow")})
    n_v4 = n_cp = 0
    for lp in cow_lps:
        sy = lp_syms[lp]
        proto = "UP (cow AMM)" if "up33" in sy.lower() else "CoW AMM (Uniswap)"
        mints = await ch.q(f"SELECT tx_hash, block_number, to_addr, value FROM rh.token_transfers "
                           f"WHERE token='{lp}' AND kind='erc20' AND from_addr='{ZERO}' "
                           f"ORDER BY block_number DESC LIMIT 6", timeout=60)
        meta = ""
        for m in mints:
            lg = await ch.q(f"SELECT topic1, topic2, data FROM rh.logs WHERE block_number={m['block_number']} "
                            f"AND tx_hash='{m['tx_hash']}' AND address='{V4_PM}' AND topic0='{ML}' LIMIT 1", timeout=60)
            if lg:
                s24 = lambda v: v - (1 << 24) if v >= (1 << 23) else v
                pool, owner = lg[0]["topic1"], "0x" + lg[0]["topic2"][-40:]
                # the manager rebalances: collect EVERY range it ever touched in this pool
                allr = await ch.q(f"SELECT DISTINCT substring(data, 3, 64) AS tl, substring(data, 67, 64) AS tu, "
                                  f"substring(data, 195, 64) AS salt FROM rh.logs WHERE address='{V4_PM}' AND topic0='{ML}' "
                                  f"AND topic1='{pool}' AND topic2='{lg[0]['topic2']}' LIMIT 200", timeout=600)
                ranges = [{"tl": s24(int(r["tl"], 16) & 0xFFFFFF), "tu": s24(int(r["tu"], 16) & 0xFFFFFF),
                           "salt": "0x" + r["salt"]} for r in allr]
                meta = json.dumps({"mode": "v4", "pool": pool, "owner": owner, "ranges": ranges})
                n_v4 += 1
                break
            tr = await ch.q(f"SELECT token, from_addr, to_addr, value FROM rh.token_transfers "
                            f"WHERE block_number={m['block_number']} AND tx_hash='{m['tx_hash']}' AND kind='erc20'", timeout=60)
            tsy = await node.resolve_symbols(list({t["token"] for t in tr}))
            a, b = sy.rsplit("-", 1)
            want = {a.split("robinhood", 1)[-1].lower() if "robinhood" in a.lower() else a.lower(), b.lower()}
            # deposits = the named tokens leaving the LP receiver in this tx
            dep = {}
            for t in tr:
                if tsy.get(t["token"], "").lower() in want and t["from_addr"] == m["to_addr"]:
                    dep[t["token"]] = dep.get(t["token"], 0) + float(t["value"])
            if len(dep) == 2 and float(m["value"]) > 0:
                (x, x0), (y, y0) = dep.items()
                meta = json.dumps({"mode": "cp", "x": x, "y": y, "x0": x0, "y0": y0, "l0": float(m["value"])})
                n_cp += 1
                break
        rows.append((lp, "cow_lp", proto, meta))
    print(f"  cow LPs: {len(cow_lps)} (v4-backed {n_v4}, constant-product {n_cp}, unresolved {len(cow_lps)-n_v4-n_cp})", flush=True)

    # lockers
    lockers = await emitters(topic("Locked(address,uint256,uint256)"), lo, hi, 50)
    for l in lockers:
        rows.append((l, "locker", "locker (UNCX-style)", ""))
    print(f"  lockers: {len(lockers)}", flush=True)

    # Morpho Blue markets
    mk = await ch.q(f"SELECT DISTINCT topic1 AS id FROM rh.logs WHERE address='{MORPHO}' AND "
                    f"topic0='{topic('CreateMarket(bytes32,(address,address,address,address,uint256))')}'",
                    timeout=600)
    for m in mk:
        rows.append((m["id"], "morpho_market", "Morpho Blue", ""))
    print(f"  morpho markets: {len(mk)}", flush=True)

    # Ramses/GIGA pairs
    n = int(await node.eth_call(RAMSES_FACTORY, sel("allPairsLength()")), 16)
    pairs = []
    for i in range(n):
        r = await node.eth_call(RAMSES_FACTORY, sel("allPairs(uint256)") + hex(i)[2:].rjust(64, "0"))
        if r not in ("0x", ""):
            pairs.append("0x" + r[-40:].lower())
    psyms = await node.resolve_symbols(pairs)
    for p in pairs:
        rows.append((p, "pool_token", "Ramses/GIGA", psyms.get(p, "")))
    print(f"  ramses pairs: {len(pairs)}", flush=True)

    await ch_write("TRUNCATE TABLE rh.protocol_registry")
    esc = lambda x: str(x).replace("\\", "\\\\").replace("'", "\\'")
    vals = ",".join("('{}','{}','{}','{}')".format(*[esc(x) for x in r]) for r in rows)
    await ch_write("INSERT INTO rh.protocol_registry (address, kind, protocol, meta) VALUES " + vals)
    print(f"protocol_registry = {await ch.scalar('SELECT count() FROM rh.protocol_registry')} rows", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
