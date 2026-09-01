"""Analysis-object builders. Each returns a structured object the OPRAI LLM
narrates into a long report and the frontend renders as charts/tables:

  { subject, status, kpis[], charts[], tables[], facts{} }

Queries the raw index (available now) + derived tables (after transforms.sql).
Degrades gracefully: smart-money / P&L fields fill in once derived tables exist.
"""
from __future__ import annotations

from . import ch, node
from .ch import addr, USDG


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
    if fb:
        fbrow = await ch.one(
            f"SELECT sum(toFloat64(value)) AS bought, uniqExact(to_addr) AS buyers {base} "
            f"AND block_number={fb} AND from_addr!='0x0000000000000000000000000000000000000000'")
        launch_bundle_pct = round(min(100.0, 100 * float(fbrow.get("bought") or 0) / minted), 1) if minted else 0.0
        launch_buyers = int(fbrow.get("buyers") or 0)

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
    dev_is_launchpad = dev_tokens > 100
    if dev_is_launchpad:
        dev_tokens, dev_rugs = 0, 0

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
             else {"label": "Dev holding", "value": "Launched via a shared launchpad — individual creator not identified"}
             if dev_status == "via_launchpad"
             else {"label": "Dev holding", "value": dev_holding_pct, "fmt": "%"}),
            {"label": "Supply grabbed at launch", "value": launch_bundle_pct, "fmt": "%"},
            {"label": "Launch-block buyers", "value": launch_buyers},
            {"label": "Smart-money holders", "value": smart_holders},
            {"label": "Smart-money supply", "value": smart_holding_pct, "fmt": "%"},
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
            "dev_is_launchpad": dev_is_launchpad,
            "dev_tokens_created": dev_tokens, "dev_rug_count": dev_rugs,
            "dev_holding_pct": dev_holding_pct, "launch_bundle_pct": launch_bundle_pct,
            "launch_block_buyers": launch_buyers,
            "launch_bundle_signal": launch_bundle_signal,
            "smart_money_holders": smart_holders,
            "smart_money_holding_pct": smart_holding_pct,
            "risk_score": risk, "risk_label": risk_label,
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
    tokens = await ch.q(f"""
        SELECT token, countIf(to_addr='{w}') AS received, countIf(from_addr='{w}') AS sent,
               (sumIf(toFloat64(value), to_addr='{w}') - sumIf(toFloat64(value), from_addr='{w}'))/1e18 AS net,
               minIf(timestamp, to_addr='{w}') AS first_in,
               maxIf(timestamp, from_addr='{w}') AS last_out
        FROM rh.token_transfers WHERE (to_addr='{w}' OR from_addr='{w}') AND kind='erc20'
        GROUP BY token ORDER BY (received+sent) DESC LIMIT 40""")

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
    archetype = "unknown"
    if txs_per_active_day >= 200:
        archetype = "bot / HFT"
    elif median_hold_h is not None and median_hold_h < 24:
        archetype = "paperhand"
    elif median_hold_h is not None and median_hold_h > 30 * 24:
        archetype = "diamond hand"
    elif len(holdings) > 0 and n_sells < n_buys * 0.3:
        archetype = "accumulator"

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
            evs = await ch.q(f"""
                SELECT toDate(tt.timestamp) AS day, tt.token AS token,
                       if(tt.to_addr='{w}', 1, -1) AS dir,
                       toFloat64(tt.value)/1e18 AS qty, p.price_usd AS px
                FROM rh.token_transfers tt
                INNER JOIN rh.token_prices p
                       ON tt.token=p.token AND toDate(tt.timestamp)=toDate(p.timestamp)
                WHERE (tt.to_addr='{w}' OR tt.from_addr='{w}') AND tt.kind='erc20'
                  AND p.price_usd > 0 AND p.price_usd < 1e6
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
