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
    t = addr(token)
    base = f"FROM rh.token_transfers WHERE token='{t}' AND kind='erc20'"

    overview = await ch.one(f"""
        SELECT uniqExact(to_addr) AS holders, count() AS transfers,
               min(block_number) AS first_block, min(timestamp) AS first_ts,
               max(timestamp) AS last_ts, uniqExact(tx_hash) AS txs {base}""")

    top_holders = await ch.q(f"""
        SELECT holder, sum(net)/1e18 AS balance FROM (
          SELECT to_addr AS holder, toFloat64(value) AS net {base}
          UNION ALL SELECT from_addr, -toFloat64(value) {base}
        ) WHERE holder NOT IN ('0x','0x0000000000000000000000000000000000000000')
        GROUP BY holder HAVING balance > 0 ORDER BY balance DESC LIMIT 20""")

    snipers = await ch.q(f"""
        SELECT to_addr AS wallet, block_number, timestamp {base}
        AND from_addr='0x0000000000000000000000000000000000000000'
        ORDER BY block_number, log_index LIMIT 20""")

    first_blocks = await ch.q(f"""
        SELECT block_number, uniqExact(to_addr) AS buyers {base}
        GROUP BY block_number ORDER BY block_number LIMIT 5""")

    daily = await ch.q(f"""
        SELECT toDate(timestamp) AS day, count() AS transfers, uniqExact(to_addr) AS buyers
        {base} GROUP BY day ORDER BY day""")

    # holder concentration (top10 vs rest)
    total_supply = sum(float(h["balance"]) for h in top_holders) or 1.0
    top10 = sum(float(h["balance"]) for h in top_holders[:10])
    top10_pct = 100.0 * top10 / total_supply if total_supply else 0.0

    # smart-money participation (once smart_wallets exists)
    smart_holders = 0
    if await ch.table_exists("smart_wallets"):
        smart_holders = await ch.scalar(f"""
            SELECT uniqExact(tt.to_addr) FROM rh.token_transfers tt
            INNER JOIN rh.smart_wallets sw ON tt.to_addr=sw.wallet
            WHERE tt.token='{t}' AND tt.kind='erc20'""") or 0

    bundle_first = int(first_blocks[0]["buyers"]) if first_blocks else 0

    return {
        "subject": {"type": "token", "address": t},
        "status": "ok",
        "kpis": [
            {"label": "Holders", "value": overview.get("holders")},
            {"label": "Transfers", "value": overview.get("transfers")},
            {"label": "Top-10 concentration", "value": round(top10_pct, 1), "fmt": "%"},
            {"label": "First-block buyers (bundle?)", "value": bundle_first},
            {"label": "Smart-money holders", "value": smart_holders},
        ],
        "charts": [
            {"type": "bar", "title": "Top holders", "items":
                [{"label": h["holder"][:10] + "…", "value": round(float(h["balance"]), 2)} for h in top_holders[:12]]},
            {"type": "line", "title": "Daily transfers", "x": [d["day"] for d in daily],
             "series": [{"name": "transfers", "data": [d["transfers"] for d in daily]},
                        {"name": "unique buyers", "data": [d["buyers"] for d in daily]}]},
            {"type": "donut", "title": "Concentration",
             "items": [{"label": "Top 10", "value": round(top10_pct, 1)},
                       {"label": "Rest", "value": round(100 - top10_pct, 1)}]},
        ],
        "tables": [
            {"title": "Top holders", "columns": ["wallet", "balance"],
             "rows": [[h["holder"], round(float(h["balance"]), 2)] for h in top_holders]},
            {"title": "Snipers (first buyers)", "columns": ["wallet", "block", "time"],
             "rows": [[s["wallet"], s["block_number"], s["timestamp"]] for s in snipers]},
        ],
        "facts": {
            "holders": overview.get("holders"), "transfers": overview.get("transfers"),
            "first_seen": overview.get("first_ts"), "top10_pct": round(top10_pct, 1),
            "first_block_buyers": bundle_first, "smart_money_holders": smart_holders,
            "bundle_signal": bundle_first >= 3,
        },
    }


async def wallet_report(wallet: str) -> dict:
    w = addr(wallet)

    act = await ch.one(f"""
        SELECT count() AS txs, uniqExact(to_addr) AS distinct_dests,
               min(timestamp) AS first_seen, max(timestamp) AS last_seen
        FROM rh.transactions WHERE from_addr='{w}'""")

    tokens = await ch.q(f"""
        SELECT token, countIf(to_addr='{w}') AS received, countIf(from_addr='{w}') AS sent,
               (sum(if(to_addr='{w}', toFloat64(value), 0)) -
                sum(if(from_addr='{w}', toFloat64(value), 0)))/1e18 AS net
        FROM rh.token_transfers WHERE (to_addr='{w}' OR from_addr='{w}') AND kind='erc20'
        GROUP BY token ORDER BY (received+sent) DESC LIMIT 25""")

    daily = await ch.q(f"""
        SELECT toDate(timestamp) AS day, count() AS txs
        FROM rh.transactions WHERE from_addr='{w}' GROUP BY day ORDER BY day""")

    # P&L / smart classification (once wallet_metrics exists)
    metrics = {}
    if await ch.table_exists("wallet_metrics"):
        metrics = await ch.one(f"SELECT * FROM rh.wallet_metrics FINAL WHERE wallet='{w}'")

    kpis = [
        {"label": "Transactions", "value": act.get("txs")},
        {"label": "Tokens touched", "value": len(tokens)},
        {"label": "First seen", "value": act.get("first_seen")},
        {"label": "Last seen", "value": act.get("last_seen")},
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
            {"type": "bar", "title": "Token activity (net)", "items":
                [{"label": tk["token"][:10] + "…", "value": round(float(tk["net"]), 2)} for tk in tokens[:12]]},
        ],
        "tables": [
            {"title": "Tokens", "columns": ["token", "received", "sent", "net"],
             "rows": [[tk["token"], tk["received"], tk["sent"], round(float(tk["net"]), 2)] for tk in tokens]},
        ],
        "facts": {**act, "tokens_touched": len(tokens), **metrics},
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
