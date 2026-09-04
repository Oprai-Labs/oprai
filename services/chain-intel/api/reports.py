"""Analysis-object builders. Each returns a structured object the OPRAI LLM
narrates into a long report and the frontend renders as charts/tables:

  { subject, status, kpis[], charts[], tables[], facts{} }

Queries the raw index (available now) + derived tables (after transforms.sql).
Degrades gracefully: smart-money / P&L fields fill in once derived tables exist.
"""
from __future__ import annotations

from . import ch, node
from .ch import addr, USDG

# Robinhood-Chain launchpads whose tokens are identified by the ROUTER their launch
# tx was sent to. Unlike Pons (which exposes an on-chain getLaunchedToken registry)
# these have no per-token registry — but every one of their launches is a tx to the
# same router contract, so the launch tx's `to` names the launchpad. Verified by
# launch volume through each router (Noxa 58k+, LONG 11k+ distinct mints). Address
# (lowercase) -> launchpad name. Add a row per launchpad once its router is known.
_LAUNCHPAD_ROUTERS = {
    "0xd9ec2db5f3d1b236843925949fe5bd8a3836fccb": "Noxa",
    "0x22e99278308b393ea1260859b181ad7e78f5eeed": "LONG",
    # creation-tx callees confirmed from each project's own deployment docs / SDKs
    "0xc70e510e14710ea535cab7b2414860af63feab79": "Bow",                 # bow.fun V3 factory
    "0x80a77001456bc986083678f9a112b1ec2aa07281": "StonkBroker",         # Stonk Launcher (curve → V3)
    "0xd3f2cc1731b7fd17f28798835c2e02f0a1839a94": "Clanker",             # clanker-sdk clanker_v4_robinhood
    "0x16cf6788b762ee8969744586ed16fc5705140dd7": "Klik",                # Klik launch factory
    "0xeb7c034704ef8dcd2d32324c1545f62fb4ad0862": "Doppler (Airlock)",   # Bankr / LONG / Zora route here
    "0x0000ffffbe8efe702c8703ae3477ff5de3d319c0": "pools.trade",        # Uniswap liquidity launchpad launcher
}

# Uniswap V4 HOOK → the launchpad that deployed it. On this chain most launchpads are
# a V4 hook, so the hook of a token's first pool names the launchpad even when the
# creation tx went through an unknown relayer. Sources: each project's deployment
# JSON / SDK, the Uniswap hooklist, DefiLlama adapters (verified 2026-09-04).
_HOOK_LAUNCHPAD = {
    "0x4e3468951d49f2eea976ed0d6e75ffcb44a9a544": "Doppler (Bankr / LONG / Zora launches)",
    "0x48b8f6ad3a1b4aa477314c9a23035b8f84dde8cc": "Clanker",
    "0x65efdf8cce99b53c925df878df275df21cb6e8cc": "Clanker",
    "0x75a54357d9c78a2db19004a5fdc76c50f9242aec": "letscash.fun",
    "0xefe669814e5eec33406bd50ffa8331618d076aec": "letscash.fun (v1)",
    "0x745d717620052a97a22deee2e5eba59583f3e0cc": "Klik",
    "0x5cf8e499c7c466c7e2cf127bdf129f57151e65dc": "Flaunch",
    "0x07f7850aa55ffb4a6f2693d493c4477747ec6fdc": "Flaunch",
    "0x14bcc18fdb0e7a427122b9c2f1a40ff7d63eaacc": "PumpV4Hook launchpad (brand unconfirmed)",
    "0x778b0c4eea7d35d66513b587ba87fc9084b0eacc": "o1 Launchpad (stocks/RWA)",
    "0x441f773b3bb1ed4c6457d0528624112e43c02acc": "o1 Launchpad",
    "0x0310cfebe1d7a69f2414f6595bbe9d17c5342acc": "o1 Launchpad",
    "0x16d1560630ce74af4478d9b8ad46548a092a2000": "PAIR (pair.fund)",
    "0xe5e702641ea86f4ae6cc3cdaed2b886f976be044": "Pons V2",
    "0xf7521cf0bb7c11e2d2794189412614cf2e29a0cc": "lunch.fun",
    "0xbffe76cc9e506285032b2e5d1b74b579e39ac0cc": "Livo",
    "0x54198ff2fce9b0df255051d49748fe53a8e428cc": "pmav.fun",
    "0x2380abf72c17aabab76480244759ac7e2932eecc": "Bags",
}


async def _hook_launchpad(t: str) -> str | None:
    """Launchpad named by a KNOWN launchpad hook on one of the token's V4 pools — the
    earliest such pool wins (a token's very first pool is often an unhooked side pool)."""
    known = "','".join(_HOOK_LAUNCHPAD)
    try:
        r = await ch.one(f"SELECT hooks FROM rh.dex_pools WHERE (token0='{t}' OR token1='{t}') "
                         f"AND dex='uniswap-v4' AND hooks IN ('{known}') ORDER BY created_block ASC LIMIT 1")
    except Exception:
        return None
    h = (r or {}).get("hooks") or ""
    return _HOOK_LAUNCHPAD.get(h.lower())
_ZERO = "0x0000000000000000000000000000000000000000"
_DEAD = "0x000000000000000000000000000000000000dead"
# Uniswap V4 is a SINGLETON: every V4 trade on this chain moves tokens against this
# one PoolManager, so counterparty == PoolManager is the V4-usage signal.
_V4_POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
# Which CONTRACT a wallet calls is what identifies the platform it trades on — V4
# settles with flash accounting, so the ERC20 counterparty is a router, not the pool.
# Confirmed on-chain (top callees by tx volume + method signature).
# Venue is identified by the SWAP EVENT a tx emits, not the contract it called.
_DEX_LABEL = {"uniswap-v4": "Uniswap V4", "uniswap-v3": "Uniswap V3", "uniswap-v2": "Uniswap V2",
              "pons-curve": "Pons curve"}
_SWAP_SIGS = {
    "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f": "Uniswap V4",
    "0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83": "Uniswap V4",
    "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67": "Uniswap V3",
    "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822": "Uniswap V2",
}
_PLATFORM_CONTRACTS = {
    "0x8876789976decbfcbbbe364623c63652db8c0904": "Uniswap (Universal Router)",
    "0x8366a39cc670b4001a1121b8f6a443a643e40951": "Uniswap V4 (PoolManager)",
    "0x58daec3116aae6d93017baaea7749052e8a04fa7": "Uniswap V4 (positions)",
    "0xd9ec2db5f3d1b236843925949fe5bd8a3836fccb": "Noxa (launchpad)",
    "0x22e99278308b393ea1260859b181ad7e78f5eeed": "LONG (launchpad)",
    "0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e": "Pons (launchpad)",
    "0xa5aab3f0c6eeadf30ef1d3eb997108e976351feb": "Pons V1 (launchpad)",
    "0xd3f2cc1731b7fd17f28798835c2e02f0a1839a94": "Clanker (launchpad)",
    "0x16cf6788b762ee8969744586ed16fc5705140dd7": "Klik (launchpad)",
    "0xc70e510e14710ea535cab7b2414860af63feab79": "Bow (launchpad)",
    "0x80a77001456bc986083678f9a112b1ec2aa07281": "StonkBroker (launchpad)",
    "0xeb7c034704ef8dcd2d32324c1545f62fb4ad0862": "Doppler Airlock (Bankr/LONG launches)",
    "0x0000ffffbe8efe702c8703ae3477ff5de3d319c0": "pools.trade (launchpad)",
    "0x5cf8e499c7c466c7e2cf127bdf129f57151e65dc": "Flaunch (launchpad)",
    "0x07f7850aa55ffb4a6f2693d493c4477747ec6fdc": "Flaunch (launchpad)",
    "0x0310cfebe1d7a69f2414f6595bbe9d17c5342acc": "o1 Launchpad",
    "0x9d53d5e3bd5e8d4cbfa6db1ca238aea02e651010": "Morpho Blue",
    "0x73991a25c818bf1f1128deaab1492d45638de0d3": "Uniswap V3 (positions)",
}


def _usd(v: float) -> str:
    """Compact USD string ($1.9M / $204.2K / $980) for KPI display."""
    v = float(v or 0)
    s = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e9:
        return f"{s}${a/1e9:.2f}B"
    if a >= 1e6:
        return f"{s}${a/1e6:.2f}M"
    if a >= 1e3:
        return f"{s}${a/1e3:.1f}K"
    return f"{s}${a:.0f}"


async def _router_launchpad(t: str) -> str | None:
    """Name the launchpad by the router the token's CREATION (or first-mint) tx was
    sent to — covers launchpads without an on-chain registry (Noxa, LONG). Returns
    None when the token wasn't launched through a router we recognise. Fails open."""
    try:
        r = await ch.one(f"SELECT creation_tx, creation_block FROM rh.contracts FINAL WHERE address='{t}'")
        ctx = (r or {}).get("creation_tx")
        cblk = (r or {}).get("creation_block")
        if not ctx:
            m = await ch.one(
                f"SELECT tx_hash, block_number FROM rh.token_transfers WHERE token='{t}' "
                f"AND from_addr='{_ZERO}' ORDER BY block_number ASC LIMIT 1")
            ctx = (m or {}).get("tx_hash")
            cblk = (m or {}).get("block_number")
        if not ctx:
            return None
        # `transactions` is sorted by block_number, NOT hash — a bare `WHERE hash=`
        # full-scans 515M rows (~2.7s idle, 20s+ under load). Constrain by the known
        # creation block so it's a PK-range scan (~ms).
        blk = f" AND block_number={int(cblk)}" if cblk else ""
        tx = await ch.one(f"SELECT to_addr FROM rh.transactions WHERE hash='{ctx}'{blk}")
        return _LAUNCHPAD_ROUTERS.get(((tx or {}).get("to_addr") or "").lower())
    except Exception:
        return None


async def token_report(token: str) -> dict:
    """Comprehensive token X-ray: overview, holder distribution + concentration,
    snipers (with still-holding vs dumped status), coordination/bundle, flow
    (accumulators vs distributors), holder growth, dev identity + rug history,
    smart-money participation, and a composite risk score. Every number is real."""
    t = addr(token)
    base = f"FROM rh.token_transfers WHERE token='{t}' AND kind='erc20'"

    # the bonding-curve / AMM pool receives the initial mint and would hold most of
    # the unsold supply → it dwarfs real holders. Identify it (first 0x0→X mint
    # recipient) and exclude it so concentration reflects REAL wallets, not the AMM.
    curve = await ch.scalar(
        f"SELECT to_addr {base} AND from_addr='0x0000000000000000000000000000000000000000' "
        f"ORDER BY block_number, log_index LIMIT 1") or "0x"

    overview = await ch.one(f"""
        SELECT uniqExact(to_addr) AS holders, count() AS transfers,
               min(block_number) AS first_block, min(timestamp) AS first_ts,
               max(timestamp) AS last_ts, uniqExact(tx_hash) AS txs,
               dateDiff('day', min(timestamp), max(timestamp)) AS age_days {base}""")

    # Our index is Robinhood-Chain ONLY. Zero indexed transfers => this address has
    # no Robinhood-Chain activity — it's a token on another chain (Ethereum/BSC/…) or
    # an invalid address. Say so explicitly rather than returning an all-zero report
    # that reads as a real (dead) token. The caller/LLM surfaces "Robinhood only".
    if not overview or int(overview.get("transfers") or 0) == 0:
        return {
            "subject": {"type": "token", "address": t},
            "status": "not_found",
            "reason": "No Robinhood-Chain activity is indexed for this address. Our "
                      "deep on-chain analysis currently covers Robinhood Chain only.",
            "facts": {"on_robinhood": False},
        }

    # full net-balance holder set (one pass), reused for concentration + whales
    holders = await ch.q(f"""
        SELECT holder, sum(net) AS bal FROM (
          SELECT to_addr AS holder, toFloat64(value) AS net {base}
          UNION ALL SELECT from_addr, -toFloat64(value) {base}
        ) WHERE holder NOT IN ('0x','0x0000000000000000000000000000000000000000',
                               '0x000000000000000000000000000000000000dead','{curve}')
        GROUP BY holder HAVING bal > 0 ORDER BY bal DESC LIMIT 500""")
    supply = sum(float(h["bal"]) for h in holders) or 1.0  # circulating (non-burn), incl LP pools
    # Drop CONTRACTS (LP pools, routers) from WALLET concentration — a pool holding
    # liquidity is not a whale hoarding supply, and counting it inflates the figure
    # badly (e.g. a 25%-of-supply LP read as "top wallet"). Classify on-chain.
    contract_holders = await node.contract_addresses([h["holder"] for h in holders[:40]])
    wallets = [h for h in holders if h["holder"] not in contract_holders]
    lp_pct = round(100.0 * sum(float(h["bal"]) for h in holders if h["holder"] in contract_holders) / supply, 1)
    def pct_of(n):  # concentration of the top-n real WALLETS (contracts / LP excluded)
        return round(100.0 * sum(float(h["bal"]) for h in wallets[:n]) / supply, 1)
    top10_pct, top20_pct, top50_pct = pct_of(10), pct_of(20), pct_of(50)
    whales = sum(1 for h in wallets if float(h["bal"]) / supply > 0.01)  # >1% real-wallet holders
    top_holders = [{"holder": h["holder"], "balance": float(h["bal"]) / 1e18,
                    "pct": round(100 * float(h["bal"]) / supply, 2)} for h in wallets[:20]]
    # burned supply (held by the dead address) — a deflationary signal, shown explicitly
    # instead of silently dropped, so a big burn isn't mistaken for hidden concentration.
    dead_bal = max(0.0, float(await ch.scalar(f"""
        SELECT sumIf(toFloat64(value), to_addr='0x000000000000000000000000000000000000dead')
             - sumIf(toFloat64(value), from_addr='0x000000000000000000000000000000000000dead') {base}""") or 0))
    burned_pct = round(100.0 * dead_bal / (supply + dead_bal), 1) if (supply + dead_bal) else 0.0

    # snipers: earliest buyers + whether they STILL hold (dumped = paperhand/exit).
    # ClickHouse has no correlated subqueries → LEFT JOIN buys vs sells.
    snipers = await ch.q(f"""
        WITH buys AS (
          SELECT to_addr AS wallet, min(block_number) AS b, min(timestamp) AS ts,
                 sum(toFloat64(value)) AS bought {base}
          AND from_addr='0x0000000000000000000000000000000000000000'
          GROUP BY to_addr ORDER BY b, ts LIMIT 30),
        sold AS (
          SELECT from_addr AS wallet, sum(toFloat64(value)) AS out {base}
          GROUP BY from_addr)
        SELECT b.wallet AS wallet, b.b AS block, b.ts AS time, b.bought AS bought,
               b.bought - coalesce(s.out, 0) AS still_holding
        FROM buys b LEFT JOIN sold s ON b.wallet = s.wallet
        ORDER BY b.b, b.ts""")
    dumped = sum(1 for s in snipers if float(s.get("still_holding", 0)) <= 0)
    sniper_dump_rate = round(100 * dumped / len(snipers), 1) if snipers else 0.0

    # coordination: same-block buyer clusters (bundles) across the early window
    clusters = await ch.q(f"""
        SELECT block_number, uniqExact(to_addr) AS buyers {base}
        AND from_addr='0x0000000000000000000000000000000000000000'
        GROUP BY block_number HAVING buyers >= 3 ORDER BY block_number LIMIT 200""")
    bundle_blocks = len(clusters)
    bundle_first = int(clusters[0]["buyers"]) if clusters and clusters[0]["block_number"] == overview.get("first_block") else \
                   (max((int(c["buyers"]) for c in clusters), default=0))

    # flow: biggest net accumulators vs distributors (sell pressure read)
    flow = await ch.q(f"""
        SELECT holder, sum(net)/1e18 AS net FROM (
          SELECT to_addr AS holder, toFloat64(value) AS net {base}
          UNION ALL SELECT from_addr, -toFloat64(value) {base}
        ) WHERE holder NOT IN ('0x','0x0000000000000000000000000000000000000000')
        GROUP BY holder ORDER BY abs(net) DESC LIMIT 20""")

    # daily transfers + cumulative holder growth
    daily = await ch.q(f"""
        SELECT toDate(timestamp) AS day, count() AS transfers, uniqExact(to_addr) AS buyers
        {base} GROUP BY day ORDER BY day""")

    # dev identity + rug history. Prefer launch_creators (TRUE launcher = tx.from of
    # the on-chain launch event) over the contracts mint-sender proxy: the mint can be
    # relayed by a factory, but the launch call's sender is always the real dev.
    dev, dev_tokens, dev_rugs, dev_src = "", 0, 0, ""
    if await ch.table_exists("launch_creators"):
        lc = await ch.one(f"SELECT dev FROM rh.launch_creators FINAL WHERE token='{t}'")
        if lc.get("dev"):
            dev, dev_src = lc["dev"], "launch_creators"
    if not dev and await ch.table_exists("contracts"):
        c = await ch.one(f"SELECT deployer FROM rh.contracts FINAL WHERE address='{t}'")
        if c.get("deployer"):
            dev, dev_src = c["deployer"], "contracts"
    if dev and dev_src == "launch_creators":
        dev_tokens = int(await ch.scalar(f"SELECT count() FROM rh.launch_creators FINAL WHERE dev='{dev}'") or 0)
        if await ch.table_exists("token_metrics"):
            dev_rugs = int(await ch.scalar(f"""SELECT count() FROM rh.token_metrics FINAL
                WHERE holders < 20 AND token IN
                (SELECT token FROM rh.launch_creators FINAL WHERE dev='{dev}')""") or 0)
    elif dev and dev_src == "contracts":
        dev_tokens = int(await ch.scalar(f"SELECT count() FROM rh.contracts FINAL WHERE deployer='{dev}' AND is_token=1") or 0)
        if await ch.table_exists("token_metrics"):
            dev_rugs = int(await ch.scalar(f"""SELECT count() FROM rh.token_metrics FINAL
                WHERE holders < 20 AND token IN
                (SELECT address FROM rh.contracts FINAL WHERE deployer='{dev}' AND is_token=1)""") or 0)

    # smart-money participation (once smart_wallets exists)
    smart_holders = 0
    if await ch.table_exists("smart_wallets"):
        smart_holders = await ch.scalar(f"""
            SELECT uniqExact(tt.to_addr) FROM rh.token_transfers tt
            INNER JOIN rh.smart_wallets sw ON tt.to_addr=sw.wallet
            WHERE tt.token='{t}' AND tt.kind='erc20'""") or 0

    # Smart-money DOLLAR flow: how much these profitable wallets BOUGHT vs SOLD of
    # this token in USD (each transfer valued at its price via an ASOF join to the
    # price series). Net > 0 = accumulation, < 0 = distribution. Holder count / share
    # alone can't tell "smart money is loading up" from "smart money is cashing out".
    smart_bought_usd = smart_sold_usd = 0.0
    smart_sellers = 0
    if smart_holders and await ch.table_exists("token_prices"):
        # smart wallets' buys and sells of this token, in USD at trade time — from the
        # actor-keyed trades table (projection by token), no price join needed
        sf = await ch.one(f"""
          SELECT sumIf(t.usd, t.side='buy') AS bought, sumIf(t.usd, t.side='sell') AS sold,
                 uniqExactIf(t.actor, t.side='sell') AS sellers
          FROM rh.trades t INNER JOIN rh.smart_wallets sw ON sw.wallet = t.actor
          WHERE t.token='{t}'""", timeout=10)
        smart_bought_usd = round(float(sf.get("bought") or 0), 2)
        smart_sold_usd = round(float(sf.get("sold") or 0), 2)
        smart_sellers = int(sf.get("sellers") or 0)
    smart_net_usd = round(smart_bought_usd - smart_sold_usd, 2)

    # dev behaviour: does the launcher still hold, or did they dump? + how much of
    # supply was grabbed in the LAUNCH block (the classic bundle/insider snipe).
    # total minted (= total supply) is the honest denominator for dev / bundle %.
    minted = float(await ch.scalar(
        f"SELECT sum(toFloat64(value)) {base} AND from_addr='0x0000000000000000000000000000000000000000'") or 0) or supply
    dev_holding_pct = 0.0
    if dev:
        dev_bal = await ch.scalar(
            f"SELECT sumIf(toFloat64(value), to_addr='{dev}') - sumIf(toFloat64(value), from_addr='{dev}') {base}") or 0
        dev_holding_pct = round(min(100.0, max(0.0, 100 * float(dev_bal) / minted)), 1) if minted else 0.0

    launch_bundle_pct, launch_buyers = 0.0, 0
    fb = overview.get("first_block")
    if fb and minted:
        # A launch BUNDLE is a coordinated grab by multiple WALLETS in the launch
        # block — NOT the launch plumbing moving supply mint -> curve -> LP pool,
        # which is all contract->contract and would otherwise read as a fake
        # "100% by 2 buyers". Count per-recipient first-block receipts, drop mints,
        # and drop CONTRACT recipients (pools / curve / router) — same wallet-only
        # rule as holder concentration — so only real wallet buys count.
        fb_rows = await ch.q(
            f"SELECT to_addr, sum(toFloat64(value)) AS got {base} "
            f"AND block_number={fb} AND from_addr!='{_ZERO}' GROUP BY to_addr")
        fb_contracts = await node.contract_addresses([r["to_addr"] for r in fb_rows])
        fb_wallets = [r for r in fb_rows if r["to_addr"] not in fb_contracts]
        bought = sum(float(r.get("got") or 0) for r in fb_wallets)
        launch_bundle_pct = round(min(100.0, 100 * bought / minted), 1)
        launch_buyers = len(fb_wallets)

    # Smart-money's share of SUPPLY (not just the holder count) — the honest read
    # of how much of the token profitable wallets actually hold.
    smart_holding_pct = 0.0
    if smart_holders and minted and await ch.table_exists("smart_wallets"):
        sb = await ch.scalar(f"""
            SELECT sumIf(toFloat64(value), tt.to_addr=sw.wallet)
                 - sumIf(toFloat64(value), tt.from_addr=sw.wallet)
            FROM rh.token_transfers tt INNER JOIN rh.smart_wallets sw
              ON (tt.to_addr=sw.wallet OR tt.from_addr=sw.wallet)
            WHERE tt.token='{t}' AND tt.kind='erc20'""") or 0
        smart_holding_pct = round(min(100.0, max(0.0, 100 * float(sb) / minted)), 1)

    # A "dev" credited with 100s+ launches is a shared LAUNCHPAD/factory relayer
    # (every launch's tx.from is the same hot wallet), NOT this token's individual
    # creator. Its aggregate launch/rug counts belong to the LAUNCHPAD, not this
    # token — how many tokens a launchpad has minted or how many later rugged is not
    # a signal about THIS project, so drop those numbers entirely (don't surface them).
    # Launchpad attribution — two COMPLEMENTARY sources, each named specifically so
    # a Pons token, a pools.trade token and a Noxa/LONG token read differently:
    #   • on-chain factory (Pons, …) — the launcher is a shared relayer, so the
    #     individual creator is NOT recoverable on-chain; name the launchpad only.
    #   • pools.trade tRPC — for ITS OWN bonding-curve / CCA launches it exposes both
    #     the launchpad name AND the real creator EOA, so we can still report that
    #     creator's ACTUAL holding rather than hiding behind the launchpad.
    onchain_lp = await node.detect_launchpad(t)          # e.g. "Pons", else None
    router_lp = await _router_launchpad(t)               # e.g. "Noxa"/"LONG", else None
    hook_lp = await _hook_launchpad(t)                   # e.g. "Clanker"/"Flaunch"/"o1", else None
    pt = await node.pools_trade_launch(t)                # {'launchpad','creator'} | None
    launchpad_source = ("factory" if onchain_lp else "creation router" if router_lp else
                        "uniswap-v4 hook" if hook_lp else "pools.trade" if pt else None)
    pt_creator = (pt or {}).get("creator")

    # The launch tx sender is the dev — for Pons (and every launchpad whose launch event
    # we index) it is a real EOA, verified: launch_creators.dev == tx.from on 300/300 launches.
    lc = await ch.one(f"SELECT dev FROM rh.launch_creators WHERE token='{t}' ORDER BY block DESC LIMIT 1")
    lc_dev = (lc or {}).get("dev") or ""
    if lc_dev and not (pt and pt_creator):
        launchpad = onchain_lp or router_lp or hook_lp or (pt["launchpad"] if pt else None)
        dev, dev_src = lc_dev, "launch tx sender"
        dev_tokens = int(await ch.scalar(f"SELECT uniqExact(token) FROM rh.launch_creators WHERE dev='{dev}'") or 0)
        dev_bal = await ch.scalar(
            f"SELECT sumIf(toFloat64(value), to_addr='{dev}') - sumIf(toFloat64(value), from_addr='{dev}') {base}") or 0
        dev_holding_pct = round(min(100.0, max(0.0, 100 * float(dev_bal) / minted)), 1) if minted else 0.0
        dev_is_launchpad = False
        launchpad_source = launchpad_source or "launch event"
        try:
            dev_rugs = int(await ch.scalar(f"""SELECT countIf(drawdown >= 0.95 AND last_trade_ts < now() - INTERVAL 3 DAY)
                FROM rh.token_stats WHERE dev='{dev}'""") or 0)
        except Exception:
            pass
    elif pt and pt_creator:
        # pools.trade launch with a KNOWN creator — treat that EOA as the dev, drop
        # any shared-relayer launch/rug stats, and report its real holding.
        launchpad, dev_src, dev_tokens, dev_rugs = pt["launchpad"], "pools.trade", 0, 0
        dev = pt_creator
        dev_bal = await ch.scalar(
            f"SELECT sumIf(toFloat64(value), to_addr='{dev}') - sumIf(toFloat64(value), from_addr='{dev}') {base}") or 0
        dev_holding_pct = round(min(100.0, max(0.0, 100 * float(dev_bal) / minted)), 1) if minted else 0.0
        dev_is_launchpad = False
    else:
        # On-chain launchpad (Pons/…) or a "dev" credited with 100s+ launches — a
        # shared factory relayer, NOT this token's individual creator. Its aggregate
        # launch/rug counts belong to the launchpad, not this project — drop them.
        launchpad = onchain_lp or router_lp or hook_lp or (pt["launchpad"] if pt else None)
        dev_is_launchpad = launchpad is not None or dev_tokens > 100
        if dev_is_launchpad:
            dev_tokens, dev_rugs = 0, 0
        if launchpad is None and dev_is_launchpad:
            launchpad, launchpad_source = "a shared launchpad", "relayer with 100+ launches (unnamed)"

    # The dev's track record from token_stats (all their launches, any launchpad):
    # how many, where, how many graduated / reached $100K / $1M ATH, median ATH,
    # how many are dead. Absent when the dev is unknown.
    dev_history = None
    if dev and not dev_is_launchpad:
        try:
            dh = await ch.one(f"""
                SELECT count() AS n, countIf(graduated) AS graduated_n,
                       countIf(ath_mcap_usd >= 100000) AS hit_100k, countIf(ath_mcap_usd >= 1e6) AS hit_1m,
                       countIf(ath_multiple >= 10) AS runners_10x,
                       countIf(drawdown >= 0.95 AND last_trade_ts < now() - INTERVAL 3 DAY) AS dead,
                       round(median(ath_mcap_usd)) AS median_ath_mcap_usd, round(max(ath_mcap_usd)) AS best_ath_mcap_usd,
                       groupArray(8)(launchpad) AS launchpads_sample, min(launch_ts) AS first_launch, max(launch_ts) AS last_launch
                FROM rh.token_stats WHERE dev='{dev}'""", timeout=10)
            if dh and int(dh.get("n") or 0):
                n = int(dh["n"])
                by_lp = await ch.q(f"SELECT launchpad, count() AS n FROM rh.token_stats WHERE dev='{dev}' GROUP BY launchpad ORDER BY n DESC LIMIT 6", timeout=10)
                dev_history = {
                    "tokens": n, "graduated": int(dh["graduated_n"]), "hit_100k": int(dh["hit_100k"]), "hit_1m": int(dh["hit_1m"]),
                    "runners_10x": int(dh["runners_10x"]), "dead": int(dh["dead"]),
                    "hit_100k_rate": round(int(dh["hit_100k"]) / n, 3), "graduation_rate": round(int(dh["graduated_n"]) / n, 3),
                    "median_ath_mcap_usd": float(dh["median_ath_mcap_usd"] or 0), "best_ath_mcap_usd": float(dh["best_ath_mcap_usd"] or 0),
                    "by_launchpad": [{"launchpad": r["launchpad"], "tokens": int(r["n"])} for r in by_lp],
                    "first_launch": str(dh["first_launch"]), "last_launch": str(dh["last_launch"]),
                }
        except Exception:
            dev_history = None

    # Dev status — distinguish "we don't know who the dev is" (identity not indexed)
    # or "launched via a shared launchpad" from "dev holds none" (holding / sold /
    # never held). Never report a sale — or a rug history — we can't attribute.
    if not dev:
        dev_status = "unknown"          # identity not in the index — NOT "sold"
    elif dev_is_launchpad:
        dev_status = "via_launchpad"    # tx.from is a launchpad relayer, not the creator
    elif dev_holding_pct > 0:
        dev_status = "holding"
    else:
        dev_recv = await ch.scalar(f"SELECT sumIf(toFloat64(value), to_addr='{dev}') {base}") or 0
        dev_status = "sold" if float(dev_recv) > 0 else "never_held"

    # Dev DISPOSAL detail — "still holds X%" alone hides a dev who quietly spread
    # supply across fresh wallets. For a REAL creator (not a launchpad relayer),
    # classify what left the dev wallet and WHERE: sold into the pool (a contract),
    # moved to other WALLETS (distribution / OTC / sock-puppets), or burned. Computed
    # on the FINAL dev address (pools.trade's real creator when known).
    dev_moved_to_wallets_pct = dev_sold_to_pool_pct = dev_burned_pct = 0.0
    dev_out_wallets = 0
    if dev and minted and not dev_is_launchpad:
        outs = await ch.q(
            f"SELECT to_addr, sum(toFloat64(value)) AS v {base} AND from_addr='{dev}' GROUP BY to_addr")
        if outs:
            out_contracts = await node.contract_addresses([r["to_addr"] for r in outs])
            to_pool = to_wallets = to_burn = 0.0
            for r in outs:
                a, v = r["to_addr"], float(r.get("v") or 0)
                if a in (_ZERO, _DEAD):
                    to_burn += v
                elif a in out_contracts:
                    to_pool += v
                else:
                    to_wallets += v
                    dev_out_wallets += 1
            dev_moved_to_wallets_pct = round(100 * to_wallets / minted, 2)
            dev_sold_to_pool_pct = round(100 * to_pool / minted, 2)
            dev_burned_pct = round(100 * to_burn / minted, 2)

    # A launch BUNDLE is a COORDINATED grab: a large share taken at launch by
    # MULTIPLE wallets. A single launch-block buyer is just the deployer/LP holding
    # supply at block 0 — not a bundle. Guard against reading the latter as the former.
    launch_bundle_signal = launch_bundle_pct >= 50 and launch_buyers >= 2

    # composite risk score (0-100, higher = riskier) from available signals
    risk = 0
    risk += min(40, top10_pct * 0.4)                    # concentration
    risk += min(20, bundle_blocks * 3)                  # coordination
    risk += min(15, sniper_dump_rate * 0.15)            # sniper dump
    risk += min(15, launch_bundle_pct * 0.3)            # supply grabbed at launch
    risk += 8 if dev_holding_pct > 20 else 0            # dev still holds a lot (dump risk)
    risk += 12 if (dev_rugs and dev_rugs > 0 and not dev_is_launchpad) else 0  # dev rug history (not a launchpad's)
    risk = round(min(100, risk))
    risk_label = "HIGH" if risk >= 60 else "MEDIUM" if risk >= 30 else "LOW"

    # WHERE it trades: the venue map (dex_pools). Uniswap V4 is a singleton, so its
    # "pool" is a poolId and the HOOK address is what identifies the issuer/launchpad
    # behind it — that's the "Factory" line a trader wants next to the DEX name.
    venues = []
    if await ch.table_exists("dex_pools"):
        vrows = await ch.q(
            f"SELECT dex, pool, token0, token1, fee, hooks FROM rh.dex_pools "
            f"WHERE token0='{t}' OR token1='{t}' ORDER BY created_block ASC LIMIT 8")
        pairs = [(r["token1"] if r["token0"] == t else r["token0"]) for r in vrows]
        psyms = await node.resolve_symbols([p for p in pairs if p and int(p, 16) != 0])
        for r, pair in zip(vrows, pairs):
            hooks = r.get("hooks") or ""
            venues.append({
                "dex": r["dex"], "pool": r["pool"],
                "pair_token": pair,
                "pair_symbol": psyms.get(pair) or ("ETH" if pair and int(pair, 16) == 0 else None),
                # V4 encodes a hook-controlled DYNAMIC fee as 0x800000 — that's a
                # flag, not 838%. Report it as dynamic rather than a nonsense number.
                "fee_pct": (None if int(r.get("fee") or 0) == 0x800000
                            else round(int(r.get("fee") or 0) / 10000, 4)),
                "dynamic_fee": int(r.get("fee") or 0) == 0x800000,
                "hooks": hooks if hooks and int(hooks, 16) != 0 else None,
                "hook_name": _HOOK_LAUNCHPAD.get(hooks.lower()) if hooks else None,
            })

    # SCAM VERDICT — a plain answer to "is this a scam?", assembled from the signals
    # above plus a live curve read for the sell tax. Each reason is a fact the user
    # can check; we never call something a scam on the score alone.
    tax_bps = None
    try:
        st = await node.pons_curve_state(t)
        if st:
            tax_bps = int(st.get("fee_bps") or 0) + int(st.get("tax_bps") or 0)
    except Exception:
        pass
    red, amber = [], []
    if launch_bundle_signal:
        red.append(f"{launch_bundle_pct}% of supply taken at launch by {launch_buyers} wallets")
    if (dev_rugs or 0) > 0 and not dev_is_launchpad:
        red.append(f"the dev has {dev_rugs} prior token(s) that died")
    if tax_bps is not None and tax_bps >= 1000:
        red.append(f"{tax_bps/100:.0f}% sell tax")
    if top10_pct >= 60:
        red.append(f"top-10 wallets hold {top10_pct}%")
    elif top10_pct >= 40:
        amber.append(f"top-10 wallets hold {top10_pct}%")
    if sniper_dump_rate >= 80 and len(snipers) >= 3:
        amber.append(f"{sniper_dump_rate}% of snipers already dumped")
    if (dev_moved_to_wallets_pct or 0) >= 5:
        amber.append(f"the dev moved {dev_moved_to_wallets_pct}% to other wallets")
    if lp_pct < 5 and burned_pct < 5:
        amber.append("almost nothing locked in LP or burned")
    verdict = "LIKELY SCAM" if len(red) >= 2 else "HIGH RISK" if red else (
        "CAUTION" if len(amber) >= 2 else "NO SCAM SIGNALS")

    # For a launchpad-relayed token there is NO identifiable dev wallet, so "dev holds
    # 0%" / "dev burned 0%" would be a false statement about a wallet we never found
    # (and reads as contradicting the token's real, protocol-driven burn). Null these
    # out so nothing downstream reports a dev holding or disposal figure — the only
    # dev fact is dev_status=via_launchpad + the launchpad name.
    if dev_is_launchpad:
        dev_holding_pct = None
        dev_moved_to_wallets_pct = dev_sold_to_pool_pct = dev_burned_pct = None
        dev_out_wallets = None

    # on-chain identity + the materialized stats (symbol/name from the node, ATH, mcap, drawdown)
    sym_row = ts_full = None
    try:
        sym_row = await ch.one(f"SELECT symbol, name FROM rh.token_symbols WHERE token='{t}'", timeout=3)
        ts_full = await ch.one(f"SELECT ath_mcap_usd, mcap_usd, drawdown, ath_multiple FROM rh.token_stats WHERE token='{t}'", timeout=3)
    except Exception:
        pass

    # Traded volume — every swap of this token, decoded, with the quote-side USD.
    vol_by_venue: list[dict] = []
    vol_24h = vol_7d = vol_all = 0.0
    swaps_24h = swaps_all = 0
    try:
        # totals from the materialized per-token stats (ms); per-venue split from the
        # projection-keyed trades table over the last 7 days
        ts_row = await ch.one(f"SELECT vol_usd, vol_24h_usd, vol_7d_usd, trades, trades_24h FROM rh.token_stats WHERE token='{t}'", timeout=5)
        if ts_row:
            vol_all, vol_24h, vol_7d = float(ts_row["vol_usd"] or 0), float(ts_row["vol_24h_usd"] or 0), float(ts_row["vol_7d_usd"] or 0)
            swaps_all, swaps_24h = int(ts_row["trades"] or 0), int(ts_row["trades_24h"] or 0)
        vr = await ch.q(f"""
            SELECT venue AS dex, count() AS n, sum(usd) AS usd_7d, sumIf(usd, ts > now() - INTERVAL 1 DAY) AS usd_24h
            FROM rh.trades WHERE token='{t}' AND ts > now() - INTERVAL 7 DAY GROUP BY venue ORDER BY usd_7d DESC""", timeout=10)
        for r in vr:
            vol_by_venue.append({"venue": _DEX_LABEL.get(r["dex"], r["dex"]), "swaps_7d": int(r["n"]),
                                 "volume_7d_usd": round(float(r["usd_7d"] or 0), 2),
                                 "volume_24h_usd": round(float(r["usd_24h"] or 0), 2)})
        if not ts_row:   # token not yet in token_stats (brand new) — fall back to the swap table
            vr2 = await ch.q(f"""SELECT count() AS n, sum(usd) AS u, countIf(timestamp > now() - INTERVAL 1 DAY) AS n24,
                sumIf(usd, timestamp > now() - INTERVAL 1 DAY) AS u24, sumIf(usd, timestamp > now() - INTERVAL 7 DAY) AS u7
                FROM rh.dex_swaps WHERE token_in='{t}' OR token_out='{t}'""", timeout=20)
            if vr2:
                r = vr2[0]; vol_all, vol_24h, vol_7d = float(r["u"] or 0), float(r["u24"] or 0), float(r["u7"] or 0)
                swaps_all, swaps_24h = int(r["n"] or 0), int(r["n24"] or 0)
    except Exception:
        pass

    return {
        "subject": {"type": "token", "address": t},
        "status": "ok",
        "kpis": [
            {"label": "Holders", "value": overview.get("holders")},
            {"label": "Age (days)", "value": overview.get("age_days")},
            {"label": "Top-10 wallet concentration", "value": top10_pct, "fmt": "%"},
            {"label": "In LP pools", "value": lp_pct, "fmt": "%"},
            {"label": "Burned", "value": burned_pct, "fmt": "%"},
            {"label": "Whales (>1%)", "value": whales},
            {"label": "Bundle blocks", "value": bundle_blocks},
            {"label": "Sniper dump rate", "value": sniper_dump_rate, "fmt": "%"},
            ({"label": "Dev holding", "value": "Unknown — identity not indexed"}
             if dev_status == "unknown"
             else {"label": "Dev holding", "value": f"Launched via {launchpad} — individual creator not identified"}
             if dev_status == "via_launchpad"
             else {"label": "Dev holding", "value": dev_holding_pct, "fmt": "%"}),
            {"label": "Supply grabbed at launch", "value": launch_bundle_pct, "fmt": "%"},
            {"label": "Launch-block buyers", "value": launch_buyers},
            {"label": "Smart-money holders", "value": smart_holders},
            {"label": "Smart-money supply", "value": smart_holding_pct, "fmt": "%"},
            *([{"label": "Smart-money bought", "value": _usd(smart_bought_usd)},
               {"label": "Smart-money sold", "value": _usd(smart_sold_usd)},
               {"label": "Smart-money net", "value": _usd(smart_net_usd)}]
              if (smart_bought_usd or smart_sold_usd) else []),
            *([{"label": "Dev moved to other wallets", "value": dev_moved_to_wallets_pct, "fmt": "%"}]
              if (dev_moved_to_wallets_pct or 0) > 0 else []),
            *([{"label": "Dev sold into pool", "value": dev_sold_to_pool_pct, "fmt": "%"}]
              if (dev_sold_to_pool_pct or 0) > 0 else []),
            *([{"label": "Dev burned", "value": dev_burned_pct, "fmt": "%"}]
              if (dev_burned_pct or 0) > 0 else []),
            {"label": "Risk score", "value": risk, "fmt": "/100", "flag": risk_label},
        ],
        "charts": [
            {"type": "bar", "title": "Top holders (%)", "items":
                [{"label": h["holder"][:8] + "…", "value": h["pct"]} for h in top_holders[:12]]},
            {"type": "line", "title": "Daily transfers", "x": [d["day"] for d in daily],
             "series": [{"name": "transfers", "data": [d["transfers"] for d in daily]},
                        {"name": "unique buyers", "data": [d["buyers"] for d in daily]}]},
            {"type": "donut", "title": "Concentration",
             "items": [{"label": "Top 10", "value": top10_pct},
                       {"label": "11-50", "value": round(top50_pct - top10_pct, 1)},
                       {"label": "Rest", "value": round(100 - top50_pct, 1)}]},
        ],
        "tables": [
            {"title": "Top holders", "columns": ["wallet", "balance", "%"],
             "rows": [[h["holder"], round(h["balance"], 2), h["pct"]] for h in top_holders]},
            {"title": "Snipers (still holding vs dumped)", "columns": ["wallet", "block", "time", "still_holding"],
             "rows": [[s["wallet"], s["block"], s["time"],
                       "yes" if float(s.get("still_holding", 0)) > 0 else "DUMPED"] for s in snipers[:20]]},
            {"title": "Biggest movers (net)", "columns": ["wallet", "net"],
             "rows": [[f["holder"], round(float(f["net"]), 2)] for f in flow]},
        ],
        "facts": {
            "holders": overview.get("holders"), "transfers": overview.get("transfers"),
            "age_days": overview.get("age_days"), "first_seen": overview.get("first_ts"),
            "top10_pct": top10_pct, "top20_pct": top20_pct, "top50_pct": top50_pct,
            "concentration_excludes": "burn + LP pools + contracts",
            "lp_pct": lp_pct, "burned_pct": burned_pct,
            "whales_over_1pct": whales, "bundle_blocks": bundle_blocks,
            "bundle_signal": bundle_blocks > 0, "max_same_block_buyers": bundle_first,
            "sniper_count": len(snipers), "sniper_dump_rate": sniper_dump_rate,
            "developer": dev, "dev_identity_source": dev_src,
            "dev_known": bool(dev), "dev_status": dev_status,
            "dev_is_launchpad": dev_is_launchpad, "launchpad": launchpad,
            "launchpad_creator_known": bool(pt and pt_creator),
            "dev_tokens_created": dev_tokens, "dev_rug_count": dev_rugs, "dev_history": dev_history,
            "dev_holding_pct": dev_holding_pct,
            "dev_moved_to_wallets_pct": dev_moved_to_wallets_pct,
            "dev_sold_to_pool_pct": dev_sold_to_pool_pct,
            "dev_burned_pct": dev_burned_pct, "dev_out_wallets": dev_out_wallets,
            "launch_bundle_pct": launch_bundle_pct,
            "launch_block_buyers": launch_buyers,
            "launch_bundle_signal": launch_bundle_signal,
            "smart_money_holders": smart_holders,
            "smart_money_holding_pct": smart_holding_pct,
            "smart_money_bought_usd": smart_bought_usd,
            "smart_money_sold_usd": smart_sold_usd,
            "smart_money_net_usd": smart_net_usd,
            "smart_money_sellers": smart_sellers,
            "risk_score": risk, "risk_label": risk_label,
            "symbol_onchain": sym_row.get("symbol") if sym_row else None,
            "name_onchain": sym_row.get("name") if sym_row else None,
            "ath_mcap_usd": float(ts_full.get("ath_mcap_usd") or 0) if ts_full else None,
            "mcap_usd": float(ts_full.get("mcap_usd") or 0) if ts_full else None,
            "drawdown_from_ath": round(float(ts_full.get("drawdown") or 0), 3) if ts_full else None,
            "ath_multiple": round(float(ts_full.get("ath_multiple") or 0), 1) if ts_full else None,
            "venues": venues, "primary_dex": venues[0]["dex"] if venues else None,
            "volume_by_venue": vol_by_venue, "volume_24h_usd": round(vol_24h, 2),
            "volume_7d_usd": round(vol_7d, 2), "volume_total_usd": round(vol_all, 2),
            "swaps_24h": swaps_24h, "swaps_total": swaps_all,
            "venue_hook": venues[0]["hooks"] if venues else None,
            "venue_hook_name": next((v["hook_name"] for v in venues if v.get("hook_name")), None) or hook_lp,
            "launchpad_source": launchpad_source,
            "scam_verdict": verdict, "scam_red_flags": red, "scam_warnings": amber,
            "sell_tax_bps": tax_bps,
            "logo": (pt or {}).get("image"),
            "symbol": (pt or {}).get("symbol"), "name": (pt or {}).get("name"),
        },
    }


async def wallet_report(wallet: str) -> dict:
    """Comprehensive wallet profile: activity, trading behavior, archetype hints
    (bot/paperhand/diamond from raw patterns), current holdings, per-token history
    with hold time, activity timeline, top counterparties — plus P&L / win-rate /
    smart-score once the derived tables are built."""
    w = addr(wallet)

    FIFO_CAP = 300000
    has_pos = await ch.table_exists("wallet_token_positions")
    has_prices = await ch.table_exists("token_prices")
    # NB: this box is CPU/IO-saturated (the archive node + index + app share 16
    # cores at load ~15), so concurrent ClickHouse queries CONTEND and run slower
    # than sequential — keep these awaits serial. Latency is box-bound, not query-
    # design-bound; it falls once the node finishes catching up.

    act = await ch.one(f"""
        SELECT count() AS txs, uniqExact(to_addr) AS distinct_dests,
               min(timestamp) AS first_seen, max(timestamp) AS last_seen,
               uniqExact(toDate(timestamp)) AS active_days,
               uniqExact(method_id) AS distinct_methods
        FROM rh.transactions WHERE from_addr='{w}'""")

    # per-token: received/sent, net holding, first-in→last-out hold window
    # (to=w) and (from=w) as two scans so each can use its wallet-keyed projection
    # (p_to / p_from on token_transfers); an OR over both columns forces a full scan.
    tokens = await ch.q(f"""
        SELECT token, sum(r) AS received, sum(s) AS sent, (sum(vin) - sum(vout))/1e18 AS net,
               min(fi) AS first_in, max(lo) AS last_out
        FROM (
            SELECT token, count() AS r, 0 AS s, sum(toFloat64(value)) AS vin, 0.0 AS vout,
                   min(timestamp) AS fi, toDateTime(0) AS lo
            FROM rh.token_transfers WHERE to_addr='{w}' AND kind='erc20' GROUP BY token
            UNION ALL
            SELECT token, 0 AS r, count() AS s, 0.0 AS vin, sum(toFloat64(value)) AS vout,
                   toDateTime('2100-01-01') AS fi, max(timestamp) AS lo
            FROM rh.token_transfers WHERE from_addr='{w}' AND kind='erc20' GROUP BY token)
        GROUP BY token ORDER BY (received+sent) DESC LIMIT 40""", timeout=45)
    for tk in tokens:
        if str(tk.get("first_in", "")).startswith("2100"):
            tk["first_in"] = None
        if str(tk.get("last_out", "")).startswith("1970"):
            tk["last_out"] = None

    # current holdings (still holds a positive net) — the portfolio
    holdings = [tk for tk in tokens if float(tk["net"]) > 0]

    # behavior signals from raw data (archetype hints without prices)
    n_buys = sum(int(tk["received"]) for tk in tokens)
    n_sells = sum(int(tk["sent"]) for tk in tokens)
    txs = int(act.get("txs") or 0)
    active_days = int(act.get("active_days") or 1)
    txs_per_active_day = round(txs / max(1, active_days), 1)
    # median hold time across positions that were both bought and sold
    holds = [(tk["first_in"], tk["last_out"]) for tk in tokens
             if tk.get("first_in") and tk.get("last_out") and int(tk["sent"]) > 0]
    hold_hours = []
    for fi, lo in holds:
        try:
            from datetime import datetime
            h = (datetime.fromisoformat(str(lo)) - datetime.fromisoformat(str(fi))).total_seconds() / 3600
            if h >= 0:
                hold_hours.append(h)
        except Exception:
            pass
    hold_hours.sort()
    median_hold_h = round(hold_hours[len(hold_hours) // 2], 1) if hold_hours else None
    # heuristic archetype (raw-only, refined once P&L lands)
    # Best/worst single trade + traded USD volume — the headline numbers "how much
    # did they make/lose at most, how much have they moved" that the per-token table
    # buries. Junk-capped like every other position read ($100M artefact filter).
    extremes: dict = {}
    if has_pos:
        ex = await ch.one(f"""
            SELECT max(realized_pnl) AS best, min(realized_pnl) AS worst,
                   argMax(token, realized_pnl) AS best_token,
                   argMin(token, realized_pnl) AS worst_token,
                   sum(usd_in) AS bought_usd, sum(usd_out) AS sold_usd
            FROM rh.wallet_token_positions
            WHERE wallet='{w}' AND abs(usd_in) < 1e8 AND abs(usd_out) < 1e8""")
        if ex and ex.get("best") is not None:
            bought, sold = float(ex.get("bought_usd") or 0), float(ex.get("sold_usd") or 0)
            extremes = {
                "best_trade_usd": round(float(ex["best"]), 2),
                "best_trade_token": ex.get("best_token"),
                "worst_trade_usd": round(float(ex["worst"]), 2),
                "worst_trade_token": ex.get("worst_token"),
                "volume_usd": round(bought + sold, 2),
                "bought_usd": round(bought, 2), "sold_usd": round(sold, 2),
            }

    # WHICH PLATFORMS this wallet actually uses. Uniswap V4 is a singleton, so every
    # V4 trade shows up as a transfer against the PoolManager; V3/V2 show up against
    # the individual pool addresses in dex_pools; launchpad buys go through the
    # launchpad's own router. Counting the wallet's counterparties against those sets
    # answers "hangi platformları kullanmış" without a swap-level table.
    # VENUE per trade, from the EVENT the transaction emitted. Naming contracts can
    # never keep up (92% of chain traffic goes to contracts nobody has labelled), but
    # a swap always emits its venue's own Swap event — so an unknown aggregator that
    # routes into V4 is still correctly counted as a V4 trade. Bounded to the wallet's
    # recent activity and filtered on block_number (the logs primary key) so the join
    # never degenerates into a scan of 1.8B rows.
    venue_trades: list[dict] = []
    txr = await ch.q(f"""
        SELECT tx_hash, max(block_number) AS block_number FROM (
            SELECT tx_hash, block_number FROM rh.token_transfers WHERE to_addr='{w}' AND kind='erc20' ORDER BY block_number DESC LIMIT 250
            UNION ALL
            SELECT tx_hash, block_number FROM rh.token_transfers WHERE from_addr='{w}' AND kind='erc20' ORDER BY block_number DESC LIMIT 250)
        GROUP BY tx_hash ORDER BY block_number DESC LIMIT 250""", timeout=45)
    if txr:
        # block_number IN (...) — an explicit block list lets the primary key pick
        # just those granules. A BETWEEN over an active wallet's first..last block
        # spans millions of blocks and degenerates into a scan.
        blocks = sorted({int(r["block_number"]) for r in txr})
        binl = ",".join(str(b) for b in blocks)
        hinl = ",".join(f"'{r['tx_hash']}'" for r in txr)
        sinl = ",".join(f"'{s}'" for s in _SWAP_SIGS)
        vrows = await ch.q(f"""
            SELECT topic0, uniqExact(tx_hash) AS n FROM rh.logs
            WHERE block_number IN ({binl})
              AND topic0 IN ({sinl}) AND tx_hash IN ({hinl})
            GROUP BY topic0""")
        agg_v: dict[str, int] = {}
        for r in vrows:
            name = _SWAP_SIGS[r["topic0"]]
            agg_v[name] = agg_v.get(name, 0) + int(r["n"])
        # traded USD per venue — decoded swaps (dex_swaps carries the quote-side USD)
        usd_v: dict[str, float] = {}
        try:
            srows = await ch.q(f"""
                SELECT dex, sum(usd) AS usd_sum FROM rh.dex_swaps
                WHERE block_number IN ({binl}) AND tx_hash IN ({hinl}) GROUP BY dex""")
            for r in srows:
                usd_v[_DEX_LABEL.get(r["dex"], r["dex"])] = float(r["usd_sum"] or 0)
        except Exception:
            pass
        venue_trades = [{"venue": k, "trades": v, "volume_usd": round(usd_v.get(k, 0.0), 2)} for k, v in
                        sorted(agg_v.items(), key=lambda kv: kv[1], reverse=True)]

    platforms: list[dict] = []
    callees = await ch.q(f"""
        SELECT to_addr AS c, count() AS n FROM rh.transactions
        WHERE from_addr='{w}' AND to_addr != ''
        GROUP BY c ORDER BY n DESC LIMIT 40""")
    if callees:
        unknown = [r["c"] for r in callees if r["c"] not in _PLATFORM_CONTRACTS]
        dex_of = {}
        if unknown:
            inl = ",".join(f"'{a}'" for a in unknown[:40])
            dex_of = {r["pool"]: r["dex"] for r in
                      await ch.q(f"SELECT pool, dex FROM rh.dex_pools WHERE pool IN ({inl})")}
        agg: dict[str, int] = {}
        other = 0
        for r in callees:
            name = _PLATFORM_CONTRACTS.get(r["c"]) or dex_of.get(r["c"])
            if name:
                agg[name] = agg.get(name, 0) + int(r["n"])
            else:
                other += int(r["n"])
        platforms = [{"platform": k, "txs": v} for k, v in
                     sorted(agg.items(), key=lambda kv: kv[1], reverse=True)]
        if other:
            platforms.append({"platform": "other / unlabelled contracts", "txs": other})

    # heuristic archetype (raw-only, refined once P&L lands)
    archetype = "unknown"
    if txs_per_active_day >= 200:
        archetype = "bot / HFT"
    elif median_hold_h is not None and median_hold_h < 24:
        archetype = "paperhand"
    elif median_hold_h is not None and median_hold_h > 30 * 24:
        archetype = "diamond hand"
    elif len(holdings) > 0 and n_sells < n_buys * 0.3:
        archetype = "accumulator"

    # Explicit BOT and FRESH answers — "is this a bot?" and "is this a brand-new
    # wallet?" deserve a yes/no with the evidence, not an archetype string the
    # caller has to interpret. A bot trades far too fast or too much to be human.
    # ClickHouse hands timestamps back as strings over HTTP — parse before diffing.
    def _dt(v):
        from datetime import datetime
        if isinstance(v, datetime):
            return v
        try:
            return datetime.fromisoformat(str(v))
        except Exception:
            return None
    _age_days = None
    _fs, _ls = _dt(act.get("first_seen")), _dt(act.get("last_seen"))
    if _fs and _ls:
        _age_days = (_ls - _fs).days
    bot_reasons = []
    if txs_per_active_day >= 200:
        bot_reasons.append(f"{txs_per_active_day} txs per active day")
    if median_hold_h is not None and median_hold_h < 0.5 and txs >= 100:
        bot_reasons.append(f"median hold {median_hold_h}h over {txs} txs")
    if len(tokens) > 1000:
        bot_reasons.append(f"{len(tokens)} distinct tokens traded")
    bot_flags = {
        "is_bot": bool(bot_reasons),
        "bot_reasons": bot_reasons,
        "wallet_age_days": _age_days,
        "is_fresh_wallet": bool(_age_days is not None and _age_days <= 7),
    }

    daily = await ch.q(f"""
        SELECT toDate(timestamp) AS day, count() AS txs
        FROM rh.transactions WHERE from_addr='{w}' GROUP BY day ORDER BY day""")

    counterparties = await ch.q(f"""
        SELECT to_addr AS addr, count() AS txs
        FROM rh.transactions WHERE from_addr='{w}' AND to_addr NOT IN ('0x')
        GROUP BY to_addr ORDER BY txs DESC LIMIT 10""")

    # P&L / smart classification (once wallet_metrics exists)
    metrics = {}
    if await ch.table_exists("wallet_metrics"):
        metrics = await ch.one(f"SELECT * FROM rh.wallet_metrics FINAL WHERE wallet='{w}'")
    is_smart = False
    if await ch.table_exists("smart_wallets"):
        is_smart = bool(await ch.scalar(f"SELECT 1 FROM rh.smart_wallets FINAL WHERE wallet='{w}'"))

    # jeet / missed-gains: tokens this wallet SOLD before the peak. sell_price =
    # usd_out/qty_out; peak = token ATH — computed ONLY over this wallet's tokens
    # (filtering the peak subquery to the wallet's positions is critical: a global
    # max(price) over all 1.5M-token token_prices took many seconds per request).
    jeet, total_missed = [], 0
    if has_pos and has_prices:
        jeet = await ch.q(f"""
            SELECT p.token AS token,
                   p.usd_out/nullIf(p.qty_out,0) AS sell_price,
                   pk.peak AS peak_price,
                   (pk.peak - p.usd_out/nullIf(p.qty_out,0)) * p.qty_out AS missed_usd,
                   pk.peak/nullIf(p.usd_out/nullIf(p.qty_out,0),0) AS peak_mult
            FROM rh.wallet_token_positions p
            INNER JOIN (
                SELECT token, max(price_usd) AS peak FROM rh.token_prices
                WHERE token IN (SELECT token FROM rh.wallet_token_positions
                                WHERE wallet='{w}' AND qty_out>0)
                GROUP BY token) pk
              ON p.token=pk.token
            WHERE p.wallet='{w}' AND p.qty_out>0 AND p.usd_out>0 AND abs(p.usd_out)<1e8
              AND pk.peak > p.usd_out/nullIf(p.qty_out,0)
              AND pk.peak < 1000 * (p.usd_out/nullIf(p.qty_out,0))
              AND (pk.peak - p.usd_out/nullIf(p.qty_out,0)) * p.qty_out < 1e7
            ORDER BY missed_usd DESC LIMIT 15""")
        total_missed = round(sum(float(j["missed_usd"]) for j in jeet if j.get("missed_usd")), 0)

    # time-series realized P&L via TRUE FIFO lot-matching (per-lot cost basis, not
    # avg-cost). Replay this wallet's buy/sell events in chain order with a per-token
    # FIFO queue; each sell realizes qty×(sell_px − oldest_lot_cost), attributed to the
    # sell day. Daily prices are the cost/proceeds basis. Computed on-the-fly (fast).
    daily_series, cum_series = [], []
    pnl_7d = pnl_30d = pnl_total_ts = 0.0
    pnl_truncated = False; pnl_fifo_ok = False
    if has_prices:
        from collections import defaultdict, deque
        evs = []
        try:
            # bounded 12s timeout: a hyper-active infra/router address would otherwise
            # scan ~40s and stall the caller. On timeout/error we skip FIFO and fall
            # back to the avg-cost figure below.
            # the wallet's transfer ledger via the projection-keyed scans, then the daily price
            evs = await ch.q(f"""
                SELECT toDate(tt.timestamp) AS day, tt.token AS token, tt.dir AS dir,
                       toFloat64(tt.value)/1e18 AS qty, p.px AS px
                FROM (
                    SELECT timestamp, token, 1 AS dir, value, block_number, log_index FROM rh.token_transfers WHERE to_addr='{w}' AND kind='erc20'
                    UNION ALL
                    SELECT timestamp, token, -1 AS dir, value, block_number, log_index FROM rh.token_transfers WHERE from_addr='{w}' AND kind='erc20'
                ) tt
                INNER JOIN rh.token_price_daily p ON p.token = tt.token AND p.day = toDate(tt.timestamp)
                WHERE p.px > 0 AND p.px < 1e6
                ORDER BY tt.block_number, tt.log_index
                LIMIT {FIFO_CAP}""", timeout=12.0)
            pnl_fifo_ok = True
            pnl_truncated = len(evs) >= FIFO_CAP
        except Exception:
            evs = []          # too slow / errored → avg-cost fallback kicks in
        lots: dict = defaultdict(deque)   # token -> deque([qty, cost_per_unit])
        realized_by_day: dict = defaultdict(float)
        for e in evs:
            tok = e["token"]; qty = float(e["qty"]); px = float(e["px"])
            if qty <= 0 or px <= 0:
                continue
            if int(e["dir"]) > 0:                     # buy: push a lot
                lots[tok].append([qty, px])
            else:                                     # sell: FIFO-match against lots
                remaining, realized, dq = qty, 0.0, lots[tok]
                while remaining > 1e-12 and dq:
                    lot = dq[0]
                    take = min(remaining, lot[0])
                    realized += take * (px - lot[1])
                    lot[0] -= take; remaining -= take
                    if lot[0] <= 1e-12:
                        dq.popleft()
                # sells beyond tracked lots (airdrops/untracked) contribute no basis
                if abs(realized) < 1e7:               # drop scam-token artifacts
                    realized_by_day[str(e["day"])] += realized
        daily_series = [(d, round(v, 2)) for d, v in sorted(realized_by_day.items())]
        c = 0.0
        for _, v in daily_series:
            c += v; cum_series.append(round(c, 2))
        pnl_7d = round(sum(v for _, v in daily_series[-7:]), 2)
        pnl_30d = round(sum(v for _, v in daily_series[-30:]), 2)
        pnl_total_ts = round(sum(v for _, v in daily_series), 2)

    kpis = [
        {"label": "Transactions", "value": txs},
        {"label": "Active days", "value": active_days},
        {"label": "Tokens traded", "value": len(tokens)},
        {"label": "Currently holding", "value": len(holdings)},
        {"label": "Txs / active day", "value": txs_per_active_day},
        {"label": "Median hold (h)", "value": median_hold_h},
        {"label": "Archetype", "value": archetype, "flag": archetype},
        {"label": "Smart money", "value": "YES" if is_smart else "—", "flag": "smart" if is_smart else ""},
    ]
    # headline realized P&L: prefer TRUE FIFO (pnl_total_ts) when we have events,
    # fall back to the avg-cost wallet_metrics figure otherwise.
    realized_headline = pnl_total_ts if daily_series else round(float(metrics.get("realized_pnl", 0)), 2) if metrics else 0.0
    if daily_series:
        pnl_method = "fifo_partial" if pnl_truncated else "fifo"
    elif not pnl_fifo_ok and metrics:
        pnl_method = "avg_cost_fallback_infra"   # FIFO skipped (hyper-active address)
    elif metrics:
        pnl_method = "avg_cost"
    else:
        pnl_method = "none"
    kpis.append({"label": "Realized PnL", "value": realized_headline, "fmt": "$"})
    if metrics:
        kpis += [
            {"label": "Win rate", "value": round(100 * float(metrics.get("win_rate", 0)), 1), "fmt": "%"},
            {"label": "ROI", "value": round(100 * float(metrics.get("roi", 0)), 1), "fmt": "%"},
        ]
    if jeet:
        kpis.append({"label": "Left on the table", "value": total_missed, "fmt": "$", "flag": "jeet"})
    if daily_series:
        kpis += [{"label": "P&L (7d)", "value": pnl_7d, "fmt": "$"},
                 {"label": "P&L (30d)", "value": pnl_30d, "fmt": "$"}]

    return {
        "subject": {"type": "wallet", "address": w},
        "status": "ok" if metrics else "partial",  # partial = P&L pending transforms
        "kpis": kpis,
        "charts": [
            {"type": "line", "title": "Realized P&L (daily + cumulative $)",
             "x": [d for d, _ in daily_series],
             "series": [{"name": "daily", "data": [v for _, v in daily_series]},
                        {"name": "cumulative", "data": cum_series}]},
            {"type": "line", "title": "Daily activity", "x": [d["day"] for d in daily],
             "series": [{"name": "txs", "data": [d["txs"] for d in daily]}]},
            {"type": "bar", "title": "Current holdings (net)", "items":
                [{"label": tk["token"][:8] + "…", "value": round(float(tk["net"]), 2)} for tk in holdings[:12]]},
        ],
        "tables": [
            {"title": "Token history", "columns": ["token", "buys", "sells", "net"],
             "rows": [[tk["token"], tk["received"], tk["sent"], round(float(tk["net"]), 2)] for tk in tokens[:25]]},
            {"title": "Top counterparties", "columns": ["address", "txs"],
             "rows": [[c["addr"], c["txs"]] for c in counterparties]},
            {"title": "Jeet / missed gains (sold before the peak)",
             "columns": ["token", "sell $", "peak $", "peak ×", "left on table $"],
             "rows": [[j["token"], round(float(j["sell_price"]), 6), round(float(j["peak_price"]), 6),
                       round(float(j.get("peak_mult") or 0), 1), round(float(j["missed_usd"]), 0)] for j in jeet]},
        ],
        "facts": {
            # metrics FIRST so the freshly-computed fields below (archetype,
            # active_days, first/last_seen, txs) win over stale wallet_metrics
            # columns (archetype DEFAULT '', epoch first_seen, span active_days).
            **metrics,
            "txs": txs, "active_days": active_days, "tokens_traded": len(tokens),
            "holding_count": len(holdings), "buys": n_buys, "sells": n_sells,
            "txs_per_active_day": txs_per_active_day, "median_hold_hours": median_hold_h,
            "archetype": archetype, "is_smart_money": is_smart,
            "first_seen": act.get("first_seen"), "last_seen": act.get("last_seen"),
            "total_missed_usd": total_missed, "jeet": jeet[:10],
            "realized_pnl": realized_headline, "pnl_method": pnl_method,
            "pnl_truncated": pnl_truncated,
            **({"pnl_note": f"P&L computed on the earliest {FIFO_CAP:,} events (address too active for a full replay)."} if pnl_truncated else {}),
            **({"pnl_note": "Address too active for on-the-fly FIFO (likely a router/infra contract) — showing avg-cost estimate."} if pnl_method == "avg_cost_fallback_infra" else {}),
            "realized_pnl_avg_cost": round(float(metrics.get("realized_pnl", 0)), 2) if metrics else 0.0,
            "pnl_7d": pnl_7d, "pnl_30d": pnl_30d, "pnl_timeseries_total": pnl_total_ts,
            "pnl_daily": [{"day": d, "realized": v} for d, v in daily_series[-90:]],
            **extremes, **bot_flags,
            "venue_trades": venue_trades,
            "top_venue": venue_trades[0]["venue"] if venue_trades else None,
            "platforms": platforms,
            "top_platform": platforms[0]["platform"] if platforms else None,
        },
    }


async def smart_money(limit: int = 50) -> dict:
    if not await ch.table_exists("smart_wallets"):
        return {"subject": {"type": "smart_money"}, "status": "computing",
                "facts": {"note": "Smart-money set builds after the ETL + transforms complete."}}
    limit = max(1, min(int(limit), 200))
    wallets = await ch.q(f"""
        SELECT wallet, round(realized_pnl,2) AS pnl, round(win_rate*100,1) AS win_pct, n_tokens
        FROM rh.smart_wallets FINAL ORDER BY smart_score DESC LIMIT {limit}""")
    inflows = []
    if await ch.table_exists("smart_money_inflows"):
        inflows = await ch.q("""
            SELECT token, round(net_inflow_usd,0) AS inflow_usd, distinct_smart_buyers, last_buy_ts
            FROM rh.smart_money_inflows FINAL WHERE window='24h'
            ORDER BY net_inflow_usd DESC LIMIT 25""")
    return {
        "subject": {"type": "smart_money"},
        "status": "ok",
        "kpis": [{"label": "Smart wallets", "value": len(wallets)},
                 {"label": "Tokens accumulating (24h)", "value": len(inflows)}],
        "charts": [
            {"type": "bar", "title": "Smart money — accumulating (24h, $)", "items":
                [{"label": i["token"][:10] + "…", "value": i["inflow_usd"]} for i in inflows[:12]]},
        ],
        "tables": [
            {"title": "Smart wallets", "columns": ["wallet", "PnL $", "win %", "tokens"],
             "rows": [[x["wallet"], x["pnl"], x["win_pct"], x["n_tokens"]] for x in wallets]},
            {"title": "Buying now (24h)", "columns": ["token", "net $", "smart buyers", "last"],
             "rows": [[i["token"], i["inflow_usd"], i["distinct_smart_buyers"], i["last_buy_ts"]] for i in inflows]},
        ],
        "facts": {"smart_count": len(wallets), "top_inflows": inflows[:10]},
    }


async def early_catchers(token: str, max_price: float | None = None, limit: int = 50) -> dict:
    t = addr(token)
    limit = max(1, min(int(limit), 200))
    rows = await ch.q(f"""
        SELECT to_addr AS wallet, block_number, timestamp, toFloat64(value)/1e18 AS amount
        FROM rh.token_transfers
        WHERE token='{t}' AND kind='erc20'
          AND from_addr='0x0000000000000000000000000000000000000000'
        ORDER BY block_number, log_index LIMIT {limit}""")
    return {
        "subject": {"type": "early_catchers", "token": t, "max_price": max_price},
        "status": "ok",
        "kpis": [{"label": "Early catchers", "value": len(rows)}],
        "tables": [{"title": "Earliest buyers", "columns": ["wallet", "block", "time", "amount"],
                    "rows": [[r["wallet"], r["block_number"], r["timestamp"], round(float(r["amount"]), 2)] for r in rows]}],
        "facts": {"count": len(rows), "note": "price filter applies once token_prices is built"},
    }


async def screen(min_pnl: float | None = None, min_win_rate: float | None = None,
                 min_tokens: int | None = None, limit: int = 40) -> dict:
    if not await ch.table_exists("wallet_metrics"):
        return {"subject": {"type": "screen"}, "status": "computing",
                "facts": {"note": "Wallet metrics build after the ETL + transforms complete."}}
    conds = ["1"]
    if min_pnl is not None:      conds.append(f"realized_pnl >= {float(min_pnl)}")
    if min_win_rate is not None: conds.append(f"win_rate >= {float(min_win_rate)}")
    if min_tokens is not None:   conds.append(f"n_tokens >= {int(min_tokens)}")
    limit = max(1, min(int(limit), 200))
    rows = await ch.q(f"""
        SELECT wallet, round(realized_pnl,2) AS pnl, round(win_rate*100,1) AS win_pct, n_tokens
        FROM rh.wallet_metrics FINAL WHERE {' AND '.join(conds)}
        ORDER BY realized_pnl DESC LIMIT {limit}""")
    return {
        "subject": {"type": "screen",
                    "criteria": {"min_pnl": min_pnl, "min_win_rate": min_win_rate, "min_tokens": min_tokens}},
        "status": "ok",
        "kpis": [{"label": "Matching wallets", "value": len(rows)}],
        "tables": [{"title": "Matches", "columns": ["wallet", "PnL $", "win %", "tokens"],
                    "rows": [[x["wallet"], x["pnl"], x["win_pct"], x["n_tokens"]] for x in rows]}],
        "facts": {"count": len(rows), "wallets": rows},
    }


async def cohort_flow(token: str, window_days: int = 7, limit: int = 20) -> dict:
    """Follow a token's cohort (its biggest current holders) — what OTHER tokens
    are they buying right now? 'What are this token's whales buying next?'"""
    t = addr(token)
    window_days = max(1, min(int(window_days), 90))
    limit = max(1, min(int(limit), 100))
    if await ch.table_exists("wallet_token_positions"):
        whales = await ch.q(f"""SELECT wallet FROM rh.wallet_token_positions
            WHERE token='{t}' AND holding>0 ORDER BY holding DESC LIMIT 100""")
    else:
        whales = await ch.q(f"""
            SELECT holder AS wallet FROM (
              SELECT to_addr AS holder, toFloat64(value) AS net FROM rh.token_transfers WHERE token='{t}' AND kind='erc20'
              UNION ALL SELECT from_addr, -toFloat64(value) FROM rh.token_transfers WHERE token='{t}' AND kind='erc20'
            ) WHERE holder NOT IN ('0x','0x0000000000000000000000000000000000000000')
            GROUP BY holder HAVING sum(net)>0 ORDER BY sum(net) DESC LIMIT 100""")
    wlist = "','".join(x["wallet"] for x in whales if ch.is_addr(x.get("wallet", "")))
    flows = []
    if wlist:
        flows = await ch.q(f"""
            SELECT token, count() AS buys, uniqExact(to_addr) AS buyers, max(timestamp) AS last_buy
            FROM rh.token_transfers
            WHERE kind='erc20' AND token != '{t}' AND to_addr IN ('{wlist}')
              AND token NOT IN ('{USDG}','0x0bd7d308f8e1639fab988df18a8011f41eacad73')
              AND from_addr != '0x0000000000000000000000000000000000000000'
              AND timestamp >= (SELECT max(timestamp) FROM rh.token_transfers) - INTERVAL {window_days} DAY
            GROUP BY token ORDER BY buyers DESC, buys DESC LIMIT {limit}""")
    return {
        "subject": {"type": "cohort_flow", "token": t, "window_days": window_days},
        "status": "ok",
        "kpis": [{"label": "Cohort size (whales)", "value": len(whales)},
                 {"label": f"Tokens they buy ({window_days}d)", "value": len(flows)}],
        "charts": [{"type": "bar", "title": f"What {t[:8]}… whales are buying ({window_days}d)",
                    "items": [{"label": f["token"][:10] + "…", "value": int(f["buyers"])} for f in flows[:12]]}],
        "tables": [{"title": "Cohort is accumulating", "columns": ["token", "whale buyers", "buys", "last buy"],
                    "rows": [[f["token"], f["buyers"], f["buys"], f["last_buy"]] for f in flows]}],
        "facts": {"cohort_size": len(whales), "flows": flows[:15]},
    }


async def honeypot(token: str, amount_tokens: float | None = None) -> dict:
    """Can you actually EXIT this token? For Pons launches, resolve the live curve
    via the node, report sellability + sell tax + exit value. Non-Pons → unverified
    (we don't assert it's safe)."""
    t = addr(token)
    try:
        st = await node.pons_curve_state(t)
    except Exception as e:
        st = None
        err = str(e)[:120]
    else:
        err = None
    if not st:
        # data-driven fallback (covers V1 / non-Pons): has anyone actually SOLD /
        # sent this token onward (holder-initiated transfer, not a mint/burn)? Many
        # distinct sellers = strong evidence it transfers/sells fine (not a honeypot);
        # only mints and no holder-initiated sends = suspicious.
        tb = (f"FROM rh.token_transfers WHERE token='{t}' AND kind='erc20' "
              f"AND from_addr NOT IN ('0x','0x0000000000000000000000000000000000000000') "
              f"AND to_addr NOT IN ('0x','0x000000000000000000000000000000000000dead')")
        # anchor recency windows to the INDEX TIP, not wall-clock now() — the index
        # lags real time (ETL tail), so now()-24h would sit entirely past the last
        # indexed block and every token would falsely read 0 recent sellers.
        row = await ch.one(
            "WITH (SELECT max(timestamp) FROM rh.token_transfers) AS tip "
            "SELECT uniqExact(from_addr) AS sellers, count() AS n, "
            "  uniqExactIf(from_addr, timestamp > tip - INTERVAL 24 HOUR) AS sellers_24h, "
            "  uniqExactIf(from_addr, timestamp > tip - INTERVAL 7 DAY)  AS sellers_7d, "
            "  maxIf(timestamp, 1) AS last_send " + tb)
        n_sellers = int(row.get("sellers") or 0)
        n_sends = int(row.get("n") or 0)
        s24 = int(row.get("sellers_24h") or 0)
        s7d = int(row.get("sellers_7d") or 0)
        last_send = str(row.get("last_send") or "")
        # recent holder-initiated sells are the strongest real evidence it's
        # CURRENTLY sellable — a honeypot can be toggled off after launch, so
        # "ever sold" is weaker than "sold in the last day".
        if s24 >= 3:
            verdict, flag = "SELLABLE (active)", "ok"
        elif s7d >= 3:
            verdict, flag = "SELLABLE (this week)", "ok"
        elif n_sellers >= 3:
            verdict, flag = "SELLABLE (stale — no recent exits)", "warn"
        else:
            verdict, flag = "SUSPICIOUS", "warn"
        sellable = n_sellers >= 3
        return {
            "subject": {"type": "honeypot", "token": t}, "status": "ok",
            "kpis": [
                {"label": "Sellable", "value": verdict, "flag": flag},
                {"label": "Sellers (24h)", "value": s24},
                {"label": "Sellers (7d)", "value": s7d},
                {"label": "Distinct sellers (all-time)", "value": n_sellers},
            ],
            "facts": {"launchpad": None, "sellable": sellable,
                      "distinct_sellers": n_sellers, "sellers_24h": s24, "sellers_7d": s7d,
                      "observed_sends": n_sends, "last_holder_send": last_send,
                      "method": "on-chain holder-initiated transfer history incl. recent windows (not a Pons V2 curve; exact sell tax not read)",
                      "note": ("Actively sold on-chain in the last 24h by multiple wallets → currently exits fine."
                               if s24 >= 3 else
                               "Sold this week by multiple wallets → exits work, but no exits in the last 24h."
                               if s7d >= 3 else
                               "Sold historically but NO recent exits — a honeypot can be toggled off; verify a live sell before buying."
                               if n_sellers >= 3 else
                               "Almost no holder-initiated sends — could be non-transferable / honeypot. Verify before buying."),
                      **({"error": err} if err else {})},
        }
    graduated = st["graduated"]
    sell_tax = st["fee_bps"] + st["tax_bps"]          # bps taken out of a sell
    qr, tr = st["quote_reserve"], st["token_reserve"]
    k = qr * tr
    exit_eth = None
    if amount_tokens and tr > 0 and not graduated:
        gross = qr - k / (tr + float(amount_tokens))   # constant-product quote out
        exit_eth = round(max(0.0, gross) * (1 - sell_tax / 10000.0), 6)
    return {
        "subject": {"type": "honeypot", "token": t}, "status": "ok",
        "kpis": [
            {"label": "Sellable", "value": "YES", "flag": "ok"},
            {"label": "Sell tax", "value": round(sell_tax / 100.0, 2), "fmt": "%"},
            {"label": "Status", "value": "graduated (Uniswap)" if graduated else "on bonding curve"},
            {"label": "Curve ETH liquidity", "value": round(st["real_quote"], 3)},
        ],
        "facts": {
            "launchpad": "Pons", "sellable": True, "graduated": graduated,
            "sell_tax_bps": sell_tax, "fee_bps": st["fee_bps"], "creator_tax_bps": st["tax_bps"],
            "graduation_threshold_eth": st["graduation_threshold"], "real_quote_eth": st["real_quote"],
            "quote_reserve": qr, "token_reserve": tr, "curve": st["curve"],
            "exit_eth_for_amount": exit_eth,
            "note": "Pons bonding-curve token — the curve always allows selling; main risk is the sell tax + slippage on your size.",
        },
    }
