#!/usr/bin/env python3
"""Robinhood Chain → ClickHouse LIVE tip-follower.

The bulk loader (rh_etl.py) is a one-shot resumable backfill that exits when it
reaches the tip it saw at startup. This follower is the opposite: it never exits.
Every cycle it ingests strictly-new blocks (watermark+1 .. head) and sleeps, so
the index tracks the chain head forever.

Design notes:
  * The raw tables are plain MergeTree (no dedup), so we must NEVER re-ingest a
    block that is already present. The watermark is max(number) FROM rh.blocks,
    and each batch writes token_transfers → logs → transactions → blocks LAST, so
    `blocks` only gains a row once its dependent rows are in. A crash therefore
    leaves at most one partial batch of orphan tx/log/transfer rows ABOVE the
    blocks watermark; we delete those once at startup, making restarts idempotent.
  * We stay CONFIRMATIONS blocks behind the very tip — Nitro's newest block can
    still move; a few blocks back it is stable.
  * The whole loop is wrapped so no transient RPC/CH error can kill the process;
    it logs and retries forever. Supervise it with restart:unless-stopped anyway.
"""
import os, sys, json, time, urllib.request, urllib.parse

RPC = os.environ.get("NODE_RPC", "http://127.0.0.1:8547")
CH  = os.environ.get("CH_URL", "http://127.0.0.1:8123/")
CHU = os.environ["CHU"]; CHP = os.environ["CHP"]
BATCH         = int(os.environ.get("FOLLOW_BATCH", "200"))     # blocks per RPC batch
SLEEP         = int(os.environ.get("FOLLOW_SLEEP", "8"))       # seconds between catch-up cycles
CONFIRMATIONS = int(os.environ.get("FOLLOW_CONFIRMATIONS", "5"))  # stay this far behind tip
TRANSFER_SIG = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def rpc(calls, tries=5):
    data = json.dumps(calls).encode()
    for a in range(tries):
        try:
            r = urllib.request.Request(RPC, data=data, headers={"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(r, timeout=120).read())
        except Exception:
            if a == tries - 1:
                raise
            time.sleep(1.5 * (a + 1))


def ch_insert(table, cols, rows_tsv, tries=5):
    if not rows_tsv:
        return
    q = f"INSERT INTO rh.{table} ({cols}) FORMAT TabSeparated"
    u = CH + "?" + urllib.parse.urlencode({"user": CHU, "password": CHP, "query": q})
    body = "\n".join(rows_tsv).encode()
    for a in range(tries):
        try:
            urllib.request.urlopen(urllib.request.Request(u, data=body), timeout=180)
            return
        except Exception as e:
            if a == tries - 1:
                sys.stderr.write(f"CH insert {table} failed: {getattr(e, 'read', lambda: b'')()[:200]}\n")
                raise
            time.sleep(1.5 * (a + 1))


def ch_query(q):
    u = CH + "?" + urllib.parse.urlencode({"user": CHU, "password": CHP, "query": q})
    return urllib.request.urlopen(u, timeout=60).read().decode()


def ch_exec(sql, tries=3):
    """Run a modifying statement (ALTER/mutation). ClickHouse treats an HTTP GET as
    readonly, so DDL/mutations MUST be POSTed (query in the request body)."""
    u = CH + "?" + urllib.parse.urlencode({"user": CHU, "password": CHP})
    for a in range(tries):
        try:
            urllib.request.urlopen(urllib.request.Request(u, data=sql.encode()), timeout=300)
            return
        except Exception:
            if a == tries - 1:
                raise
            time.sleep(1.5 * (a + 1))


def h2i(x):
    return int(x, 16) if x not in (None, "0x", "") else 0


def addr(topic):
    return "0x" + topic[-40:] if topic and len(topic) >= 42 else "0x"


def esc(s):
    return (s or "").replace("\t", " ").replace("\n", " ").replace("\\", "\\\\")


def ingest_range(lo, hi):
    """Ingest blocks (lo, hi] inclusive of hi, exclusive of lo. Returns hi on success."""
    b = lo + 1
    while b <= hi:
        n = min(BATCH, hi - b + 1)
        bcalls = [{"jsonrpc": "2.0", "id": i, "method": "eth_getBlockByNumber", "params": [hex(b + i), True]} for i in range(n)]
        rcalls = [{"jsonrpc": "2.0", "id": i, "method": "eth_getBlockReceipts", "params": [hex(b + i)]} for i in range(n)]
        bres = rpc(bcalls); rres = rpc(rcalls)
        bmap = {x["id"]: x.get("result") for x in bres}
        rmap = {x["id"]: x.get("result") for x in rres}
        blk_rows = []; tx_rows = []; log_rows = []; tt_rows = []
        for i in range(n):
            bk = bmap.get(i)
            if not bk:
                continue
            num = h2i(bk["number"]); ts = h2i(bk["timestamp"])
            txs = bk.get("transactions") or []
            blk_rows.append("\t".join(map(str, [num, bk["hash"], bk["parentHash"], ts,
                bk.get("miner", "0x"), h2i(bk.get("gasUsed")), h2i(bk.get("gasLimit")),
                h2i(bk.get("baseFeePerGas")), len(txs), h2i(bk.get("size"))])))
            for t in txs:
                inp = t.get("input", "0x")
                tx_rows.append("\t".join(map(str, [t["hash"], num, h2i(t.get("transactionIndex")), ts,
                    (t.get("from") or "0x").lower(), (t.get("to") or "0x").lower() if t.get("to") else "0x",
                    h2i(t.get("value")), h2i(t.get("gas")), h2i(t.get("gasPrice")),
                    inp[:10] if len(inp) >= 10 else inp, esc(inp), 0, h2i(t.get("nonce"))])))
            rc = rmap.get(i) or []
            for r in rc:
                txh = r.get("transactionHash"); txi = h2i(r.get("transactionIndex"))
                for lg in (r.get("logs") or []):
                    tp = lg.get("topics") or []
                    t0, t1, t2, t3 = (tp + ["", "", "", ""])[:4]
                    laddr = (lg.get("address") or "0x").lower()
                    log_rows.append("\t".join(map(str, [num, txh, txi, h2i(lg.get("logIndex")), ts,
                        laddr, t0, t1, t2, t3, esc(lg.get("data", "0x"))])))
                    if t0 == TRANSFER_SIG and len(tp) >= 3:
                        frm = addr(tp[1]).lower(); to = addr(tp[2]).lower()
                        if len(tp) == 4:  # ERC-721
                            tt_rows.append("\t".join(map(str, [num, ts, txh, h2i(lg.get("logIndex")),
                                laddr, frm, to, 0, h2i(tp[3]), "erc721"])))
                        else:            # ERC-20
                            tt_rows.append("\t".join(map(str, [num, ts, txh, h2i(lg.get("logIndex")),
                                laddr, frm, to, h2i(lg.get("data")), 0, "erc20"])))
        # write dependents FIRST, blocks LAST — so max(blocks.number) is a safe watermark
        ch_insert("token_transfers", "block_number,timestamp,tx_hash,log_index,token,from_addr,to_addr,value,token_id,kind", tt_rows)
        ch_insert("logs", "block_number,tx_hash,tx_index,log_index,timestamp,address,topic0,topic1,topic2,topic3,data", log_rows)
        ch_insert("transactions", "hash,block_number,tx_index,timestamp,from_addr,to_addr,value,gas,gas_price,method_id,input,status,nonce", tx_rows)
        ch_insert("blocks", "number,hash,parent_hash,timestamp,miner,gas_used,gas_limit,base_fee,tx_count,size", blk_rows)
        b += n
    return hi


def watermark():
    out = ch_query("SELECT max(number) FROM rh.blocks FORMAT TabSeparated").strip()
    return int(out) if out and out != "\\N" else 0


def cleanup_orphans(wm):
    """Remove any tx/log/transfer rows above the blocks watermark — leftovers from a
    batch that crashed after writing dependents but before writing `blocks`."""
    for tbl in ("transactions", "logs", "token_transfers"):
        try:
            ch_exec(f"ALTER TABLE rh.{tbl} DELETE WHERE block_number > {wm} SETTINGS mutations_sync=1")
        except Exception as e:
            _log(f"orphan cleanup {tbl} warn: {str(e)[:120]}")


def head():
    return h2i(rpc([{"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}])[0]["result"]) - CONFIRMATIONS


def main():
    wm = watermark()
    _log(f"follower start: watermark={wm} head={head()} rpc={RPC}")
    cleanup_orphans(wm)
    wm = watermark()
    while True:
        try:
            h = head()
            if h > wm:
                t0 = time.time()
                ingest_range(wm, h)
                new = watermark()
                _log(f"ingested {new - wm} blocks -> {new}  ({(time.time() - t0):.1f}s)")
                wm = new
            time.sleep(SLEEP)
        except KeyboardInterrupt:
            _log("stopping"); return
        except Exception as e:
            _log(f"cycle error (retry): {str(e)[:200]}")
            time.sleep(SLEEP * 2)


if __name__ == "__main__":
    main()
