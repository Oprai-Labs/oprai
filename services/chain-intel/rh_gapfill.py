#!/usr/bin/env python3
"""One-shot gap-filler for the Robinhood Chain index.

The original bulk backfill skipped ~0.37% of blocks (an RPC that returned null was
silently `continue`d, yet its chunk was still marked done), leaving scattered holes
below the follower's watermark. The live follower (rh_follow.py) only moves forward,
so it never fills these. This script finds the missing block numbers in windows and
ingests exactly those — no duplicates (it only fetches numbers absent from `blocks`),
safe to run alongside the follower (they touch disjoint block ranges).
"""
import os, sys, time, urllib.request, urllib.parse
from rh_follow import (rpc, ch_insert, ch_query, h2i, addr, esc, TRANSFER_SIG)

WINDOW = int(os.environ.get("GAP_WINDOW", "2000000"))   # scan window for the anti-join
BATCH  = int(os.environ.get("GAP_BATCH", "200"))         # blocks per RPC batch
HI     = int(os.environ.get("GAP_HI", "0"))              # upper bound (0 = current max(blocks))


def _log(m):
    print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)


def fetch_insert(nums):
    """Fetch and insert an explicit list of block numbers (may be non-contiguous)."""
    for i in range(0, len(nums), BATCH):
        chunk = nums[i:i + BATCH]
        bcalls = [{"jsonrpc": "2.0", "id": j, "method": "eth_getBlockByNumber", "params": [hex(b), True]} for j, b in enumerate(chunk)]
        rcalls = [{"jsonrpc": "2.0", "id": j, "method": "eth_getBlockReceipts", "params": [hex(b)]} for j, b in enumerate(chunk)]
        bres = rpc(bcalls); rres = rpc(rcalls)
        bmap = {x["id"]: x.get("result") for x in bres}
        rmap = {x["id"]: x.get("result") for x in rres}
        blk_rows = []; tx_rows = []; log_rows = []; tt_rows = []
        for j in range(len(chunk)):
            bk = bmap.get(j)
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
            for r in (rmap.get(j) or []):
                txh = r.get("transactionHash"); txi = h2i(r.get("transactionIndex"))
                for lg in (r.get("logs") or []):
                    tp = lg.get("topics") or []
                    t0, t1, t2, t3 = (tp + ["", "", "", ""])[:4]
                    laddr = (lg.get("address") or "0x").lower()
                    log_rows.append("\t".join(map(str, [num, txh, txi, h2i(lg.get("logIndex")), ts,
                        laddr, t0, t1, t2, t3, esc(lg.get("data", "0x"))])))
                    if t0 == TRANSFER_SIG and len(tp) >= 3:
                        frm = addr(tp[1]).lower(); to = addr(tp[2]).lower()
                        if len(tp) == 4:
                            tt_rows.append("\t".join(map(str, [num, ts, txh, h2i(lg.get("logIndex")), laddr, frm, to, 0, h2i(tp[3]), "erc721"])))
                        else:
                            tt_rows.append("\t".join(map(str, [num, ts, txh, h2i(lg.get("logIndex")), laddr, frm, to, h2i(lg.get("data")), 0, "erc20"])))
        ch_insert("token_transfers", "block_number,timestamp,tx_hash,log_index,token,from_addr,to_addr,value,token_id,kind", tt_rows)
        ch_insert("logs", "block_number,tx_hash,tx_index,log_index,timestamp,address,topic0,topic1,topic2,topic3,data", log_rows)
        ch_insert("transactions", "hash,block_number,tx_index,timestamp,from_addr,to_addr,value,gas,gas_price,method_id,input,status,nonce", tx_rows)
        ch_insert("blocks", "number,hash,parent_hash,timestamp,miner,gas_used,gas_limit,base_fee,tx_count,size", blk_rows)


def missing_in_window(lo, hi):
    """Block numbers in [lo, hi) that are absent from rh.blocks."""
    q = (f"SELECT number FROM numbers({lo}, {hi - lo}) "
         f"WHERE number NOT IN (SELECT number FROM rh.blocks WHERE number >= {lo} AND number < {hi}) "
         f"ORDER BY number FORMAT TabSeparated")
    out = ch_query(q).strip()
    return [int(x) for x in out.split("\n") if x.strip()] if out else []


def main():
    hi = HI or int(ch_query("SELECT max(number) FROM rh.blocks FORMAT TabSeparated").strip())
    _log(f"gap-fill scan 0..{hi} in {WINDOW}-block windows")
    total = 0
    lo = 0
    while lo < hi:
        win_hi = min(lo + WINDOW, hi)
        miss = missing_in_window(lo, win_hi)
        if miss:
            fetch_insert(miss)
            total += len(miss)
            _log(f"window {lo}-{win_hi}: filled {len(miss)}  (total {total})")
        lo = win_hi
    _log(f"DONE — filled {total} missing blocks")


if __name__ == "__main__":
    main()
