"""Post-build filter: smart_wallets must be EOAs, never contracts (pools / routers /
vaults / aggregators are not traders to follow or copy). The SQL build can't call
eth_getCode, so this over-selects candidates, drops contracts via the node, and
rebuilds smart_wallets EOA-only, re-ranked. Runs after build_metrics in the refresh."""
import asyncio, httpx
from api import ch, node
from api.ch import CH_URL, CH_USER, CH_PASS, CH_DB

async def ch_write(sql):
    params = {"user": CH_USER, "password": CH_PASS, "database": CH_DB}  # NOT readonly
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(CH_URL, params=params, content=sql.encode())
        r.raise_for_status()

async def main():
    cands = await ch.q("""SELECT wallet, smart_score, realized_pnl, win_rate, n_tokens
        FROM rh.wallet_metrics
        WHERE smart_score>0 AND n_tokens BETWEEN 3 AND 1000 AND win_rate>=0.4
        ORDER BY smart_score DESC LIMIT 9000""")
    addrs = [c["wallet"] for c in cands]
    contracts = set()
    for i in range(0, len(addrs), 500):
        contracts |= await node.contract_addresses(addrs[i:i+500])
    eoa = [c for c in cands if c["wallet"] not in contracts][:5000]
    print(f"candidates={len(cands)}  contracts_dropped={len(contracts)}  EOA_smart={len(eoa)}")

    await ch_write("TRUNCATE TABLE rh.smart_wallets")
    # batch inserts of 1000 rows
    for b in range(0, len(eoa), 1000):
        chunk = eoa[b:b+1000]
        vals = ",".join(
            f"('{c['wallet']}',{float(c['smart_score'])},{b+i+1},"
            f"{float(c['realized_pnl'])},{float(c['win_rate'])},{int(c['n_tokens'])})"
            for i, c in enumerate(chunk))
        await ch_write(
            "INSERT INTO rh.smart_wallets "
            "(wallet,smart_score,rank,realized_pnl,win_rate,n_tokens) VALUES " + vals)
    n = await ch.scalar("SELECT count() FROM rh.smart_wallets")
    ctr = len((await ch.q("SELECT wallet FROM rh.smart_wallets")))
    # verify no contracts remain (sample check on all)
    ws = [r["wallet"] for r in await ch.q("SELECT wallet FROM rh.smart_wallets")]
    still = set()
    for i in range(0, len(ws), 500):
        still |= await node.contract_addresses(ws[i:i+500])
    print(f"smart_wallets now={n}  contracts_remaining={len(still)}")

if __name__ == "__main__":
    asyncio.run(main())
