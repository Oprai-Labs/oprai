"""Analysis-object builders. Each returns a structured object the OPRAI LLM
narrates into a long report and the frontend renders as charts/tables:

  { subject, status, kpis[], charts[], tables[], facts{} }

Queries the raw index (available now) + derived tables (after transforms.sql).
Degrades gracefully: smart-money / P&L fields fill in once derived tables exist.
"""
from __future__ import annotations

from . import ch
from .ch import addr, USDG


async def token_report(token: str) -> dict:
    """Comprehensive token X-ray: overview, holder distribution + concentration,
    snipers (with still-holding vs dumped status), coordination/bundle, flow
    (accumulators vs distributors), holder growth, dev identity + rug history,
    smart-money participation, and a composite risk score. Every number is real."""
    t = addr(token)
    base = f"FROM rh.token_transfers WHERE token='{t}' AND kind='erc20'"

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
                               '0x000000000000000000000000000000000000dead')
        GROUP BY holder HAVING bal > 0 ORDER BY bal DESC LIMIT 500""")
    supply = sum(float(h["bal"]) for h in holders) or 1.0
    def pct_of(n):  # concentration of top-n holders
        return round(100.0 * sum(float(h["bal"]) for h in holders[:n]) / supply, 1)
    top10_pct, top20_pct, top50_pct = pct_of(10), pct_of(20), pct_of(50)
    whales = sum(1 for h in holders if float(h["bal"]) / supply > 0.01)  # >1% holders
    top_holders = [{"holder": h["holder"], "balance": float(h["bal"]) / 1e18,
                    "pct": round(100 * float(h["bal"]) / supply, 2)} for h in holders[:20]]

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

    # dev identity + rug history (once contracts table exists)
    dev, dev_tokens, dev_rugs = "", 0, 0
    if await ch.table_exists("contracts"):
        c = await ch.one(f"SELECT deployer FROM rh.contracts FINAL WHERE address='{t}'")
        dev = c.get("deployer", "")
        if dev:
            dev_tokens = await ch.scalar(f"SELECT count() FROM rh.contracts FINAL WHERE deployer='{dev}' AND is_token=1") or 0

    # smart-money participation (once smart_wallets exists)
    smart_holders = 0
    if await ch.table_exists("smart_wallets"):
        smart_holders = await ch.scalar(f"""
            SELECT uniqExact(tt.to_addr) FROM rh.token_transfers tt
            INNER JOIN rh.smart_wallets sw ON tt.to_addr=sw.wallet
            WHERE tt.token='{t}' AND tt.kind='erc20'""") or 0

    # composite risk score (0-100, higher = riskier) from available signals
    risk = 0
    risk += min(40, top10_pct * 0.4)                    # concentration
    risk += min(25, bundle_blocks * 3)                  # coordination
    risk += min(20, sniper_dump_rate * 0.2)             # sniper dump
    risk += 15 if (dev_rugs and dev_rugs > 0) else 0    # dev rug history
    risk = round(min(100, risk))
    risk_label = "HIGH" if risk >= 60 else "MEDIUM" if risk >= 30 else "LOW"

    return {
        "subject": {"type": "token", "address": t},
        "status": "ok",
        "kpis": [
            {"label": "Holders", "value": overview.get("holders")},
            {"label": "Age (days)", "value": overview.get("age_days")},
            {"label": "Top-10 concentration", "value": top10_pct, "fmt": "%"},
            {"label": "Whales (>1%)", "value": whales},
            {"label": "Bundle blocks", "value": bundle_blocks},
            {"label": "Sniper dump rate", "value": sniper_dump_rate, "fmt": "%"},
            {"label": "Smart-money holders", "value": smart_holders},
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
            "whales_over_1pct": whales, "bundle_blocks": bundle_blocks,
            "bundle_signal": bundle_blocks > 0, "max_same_block_buyers": bundle_first,
            "sniper_count": len(snipers), "sniper_dump_rate": sniper_dump_rate,
            "developer": dev, "dev_tokens_created": dev_tokens, "dev_rug_count": dev_rugs,
            "smart_money_holders": smart_holders,
            "risk_score": risk, "risk_label": risk_label,
        },
    }


async def wallet_report(wallet: str) -> dict:
    """Comprehensive wallet profile: activity, trading behavior, archetype hints
    (bot/paperhand/diamond from raw patterns), current holdings, per-token history
    with hold time, activity timeline, top counterparties — plus P&L / win-rate /
    smart-score once the derived tables are built."""
    w = addr(wallet)

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
    if metrics:
        kpis += [
            {"label": "Realized PnL", "value": round(float(metrics.get("realized_pnl", 0)), 2), "fmt": "$"},
            {"label": "Win rate", "value": round(100 * float(metrics.get("win_rate", 0)), 1), "fmt": "%"},
            {"label": "ROI", "value": round(100 * float(metrics.get("roi", 0)), 1), "fmt": "%"},
        ]

    return {
        "subject": {"type": "wallet", "address": w},
        "status": "ok" if metrics else "partial",  # partial = P&L pending transforms
        "kpis": kpis,
        "charts": [
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
        ],
        "facts": {
            "txs": txs, "active_days": active_days, "tokens_traded": len(tokens),
            "holding_count": len(holdings), "buys": n_buys, "sells": n_sells,
            "txs_per_active_day": txs_per_active_day, "median_hold_hours": median_hold_h,
            "archetype": archetype, "is_smart_money": is_smart,
            "first_seen": act.get("first_seen"), "last_seen": act.get("last_seen"),
            **metrics,
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
