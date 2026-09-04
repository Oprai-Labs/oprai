"""Builds the smart-wallet set: EOA-only (pools / routers / vaults are not traders to
follow or copy) and ROLLING — a wallet's all-time score decays with a 14-day half-life
of inactivity and the last 30 days' realized PnL × win rate is added on top, so a
wallet that stopped trading fades out and one that just started winning shows up
within one refresh instead of never. Over-selects candidates, drops contracts via
the node, re-ranks by the rolling score. Runs after build_metrics in the refresh."""
import asyncio, httpx
from api import ch, node
from api.ch import CH_URL, CH_USER, CH_PASS, CH_DB

HALF_LIFE_DAYS = 14

async def ch_write(sql):
    params = {"user": CH_USER, "password": CH_PASS, "database": CH_DB}  # NOT readonly
    async with httpx.AsyncClient(timeout=600) as c:
        r = await c.post(CH_URL, params=params, content=sql.encode())
        r.raise_for_status()

async def main():
    for col, typ in (("pnl_30d", "Float64"), ("win_rate_30d", "Float64"), ("n_tokens_30d", "UInt32"),
                     ("last_active", "DateTime"), ("score_rolling", "Float64")):
        await ch_write(f"ALTER TABLE rh.smart_wallets ADD COLUMN IF NOT EXISTS {col} {typ}")
    cands = await ch.q(f"""
        WITH r AS (
            SELECT wallet,
                   sumIf(realized_pnl, qty_out > 0) AS pnl_30d,
                   countIf(realized_pnl > 0 AND qty_out > 0) / nullIf(countIf(qty_out > 0), 0) AS win_30d,
                   uniqExact(token) AS n_30d, max(last_ts) AS last_active
            FROM rh.wallet_token_positions WHERE last_ts > now() - INTERVAL 30 DAY GROUP BY wallet)
        SELECT m.wallet, m.smart_score, m.realized_pnl, m.win_rate, m.n_tokens,
               ifNull(r.pnl_30d, 0) AS pnl_30d, ifNull(r.win_30d, 0) AS win_30d, ifNull(r.n_30d, 0) AS n_30d,
               if(r.last_active > toDateTime(0), r.last_active, m.last_seen) AS last_active,
               dateDiff('day', if(r.last_active > toDateTime(0), r.last_active, m.last_seen), now()) AS idle_days,
               m.smart_score * exp(-dateDiff('day', if(r.last_active > toDateTime(0), r.last_active, m.last_seen), now()) / {HALF_LIFE_DAYS} * 0.693)
                 + greatest(ifNull(r.pnl_30d, 0), 0) * ifNull(r.win_30d, 0) AS score_rolling
        FROM rh.wallet_metrics m LEFT JOIN r ON r.wallet = m.wallet
        WHERE m.smart_score > 0 AND m.n_tokens BETWEEN 3 AND 1000 AND m.win_rate >= 0.4
        ORDER BY score_rolling DESC LIMIT 9000""", timeout=600)
    addrs = [c["wallet"] for c in cands]
    contracts = set()
    for i in range(0, len(addrs), 500):
        contracts |= await node.contract_addresses(addrs[i:i+500])
    eoa = [c for c in cands if c["wallet"] not in contracts][:5000]
    print(f"candidates={len(cands)}  contracts_dropped={len(contracts)}  EOA_smart={len(eoa)}  "
          f"active_30d={sum(1 for c in eoa if int(c['n_30d']) > 0)}")

    await ch_write("TRUNCATE TABLE rh.smart_wallets")
    for b in range(0, len(eoa), 1000):
        chunk = eoa[b:b+1000]
        vals = ",".join(
            f"('{c['wallet']}',{float(c['smart_score'])},{b+i+1},{float(c['realized_pnl'])},{float(c['win_rate'])},"
            f"{int(c['n_tokens'])},{float(c['pnl_30d'])},{float(c['win_30d'])},{int(c['n_30d'])},"
            f"'{c['last_active']}',{float(c['score_rolling'])})"
            for i, c in enumerate(chunk))
        await ch_write(
            "INSERT INTO rh.smart_wallets "
            "(wallet,smart_score,rank,realized_pnl,win_rate,n_tokens,pnl_30d,win_rate_30d,n_tokens_30d,last_active,score_rolling) VALUES " + vals)
    n = await ch.scalar("SELECT count() FROM rh.smart_wallets")
    ws = [r["wallet"] for r in await ch.q("SELECT wallet FROM rh.smart_wallets")]
    still = set()
    for i in range(0, len(ws), 500):
        still |= await node.contract_addresses(ws[i:i+500])
    print(f"smart_wallets now={n}  contracts_remaining={len(still)}")

if __name__ == "__main__":
    asyncio.run(main())
