#!/usr/bin/env python3
"""Robinhood Chain → ClickHouse bulk ETL (blocks, txs, logs, token_transfers).
Parallel, resumable. Reads from the local nitro node RPC; writes via CH HTTP."""
import os, sys, json, time, urllib.request, urllib.parse, multiprocessing as mp

RPC = "http://127.0.0.1:8547"
CH  = "http://127.0.0.1:8123/"
CHU = os.environ["CHU"]; CHP = os.environ["CHP"]
CHUNK = 50_000           # blocks per progress-chunk
BATCH = 200              # blocks per RPC batch
WORKERS = int(os.environ.get("WORKERS", "8"))
TRANSFER_SIG = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

def rpc(calls, tries=5):
    data = json.dumps(calls).encode()
    for a in range(tries):
        try:
            r = urllib.request.Request(RPC, data=data, headers={"Content-Type":"application/json"})
            return json.loads(urllib.request.urlopen(r, timeout=120).read())
        except Exception as e:
            if a == tries-1: raise
            time.sleep(1.5*(a+1))

def ch_insert(table, cols, rows_tsv, tries=5):
    if not rows_tsv: return
    q = f"INSERT INTO rh.{table} ({cols}) FORMAT TabSeparated"
    u = CH + "?" + urllib.parse.urlencode({"user":CHU,"password":CHP,"query":q})
    body = "\n".join(rows_tsv).encode()
    for a in range(tries):
        try:
            urllib.request.urlopen(urllib.request.Request(u, data=body), timeout=180); return
        except Exception as e:
            if a == tries-1:
                sys.stderr.write(f"CH insert {table} failed: {getattr(e,'read',lambda:b'')()[:200]}\n"); raise
            time.sleep(1.5*(a+1))

def ch_query(q):
    u = CH + "?" + urllib.parse.urlencode({"user":CHU,"password":CHP,"query":q})
    return urllib.request.urlopen(u, timeout=60).read().decode()

def h2i(x):
    return int(x, 16) if x not in (None, "0x", "") else 0

def addr(topic):  # 32-byte topic -> 0x + last 20 bytes
    return "0x" + topic[-40:] if topic and len(topic) >= 42 else "0x"

def esc(s):       # TSV escape (hex strings are safe, but guard)
    return (s or "").replace("\t"," ").replace("\n"," ").replace("\\","\\\\")

def process_chunk(chunk):
    start = chunk * CHUNK
    end = min(start + CHUNK, TIP + 1)
    blk_rows=[]; tx_rows=[]; log_rows=[]; tt_rows=[]
    for base in range(start, end, BATCH):
        n = min(BATCH, end - base)
        bcalls=[{"jsonrpc":"2.0","id":i,"method":"eth_getBlockByNumber","params":[hex(base+i),True]} for i in range(n)]
        rcalls=[{"jsonrpc":"2.0","id":i,"method":"eth_getBlockReceipts","params":[hex(base+i)]} for i in range(n)]
        bres = rpc(bcalls); rres = rpc(rcalls)
        bmap = {x["id"]: x.get("result") for x in bres}
        rmap = {x["id"]: x.get("result") for x in rres}
        for i in range(n):
            b = bmap.get(i)
            if not b: continue
            num = h2i(b["number"]); ts = h2i(b["timestamp"])
            txs = b.get("transactions") or []
            blk_rows.append("\t".join(map(str,[num, b["hash"], b["parentHash"], ts,
                b.get("miner","0x"), h2i(b.get("gasUsed")), h2i(b.get("gasLimit")),
                h2i(b.get("baseFeePerGas")), len(txs), h2i(b.get("size"))])))
            for t in txs:
                inp = t.get("input","0x")
                tx_rows.append("\t".join(map(str,[t["hash"], num, h2i(t.get("transactionIndex")), ts,
                    (t.get("from") or "0x").lower(), (t.get("to") or "0x").lower() if t.get("to") else "0x",
                    h2i(t.get("value")), h2i(t.get("gas")), h2i(t.get("gasPrice")),
                    inp[:10] if len(inp)>=10 else inp, esc(inp), 0, h2i(t.get("nonce"))])))
            rc = rmap.get(i) or []
            for r in rc:
                st = h2i(r.get("status")); txh = r.get("transactionHash"); txi = h2i(r.get("transactionIndex"))
                for lg in (r.get("logs") or []):
                    tp = lg.get("topics") or []
                    t0,t1,t2,t3 = (tp+["","","",""])[:4]
                    laddr = (lg.get("address") or "0x").lower()
                    log_rows.append("\t".join(map(str,[num, txh, txi, h2i(lg.get("logIndex")), ts,
                        laddr, t0, t1, t2, t3, esc(lg.get("data","0x"))])))
                    if t0 == TRANSFER_SIG and len(tp) >= 3:
                        frm = addr(tp[1]).lower(); to = addr(tp[2]).lower()
                        if len(tp) == 4:  # ERC-721
                            tt_rows.append("\t".join(map(str,[num, ts, txh, h2i(lg.get("logIndex")),
                                laddr, frm, to, 0, h2i(tp[3]), "erc721"])))
                        else:            # ERC-20
                            tt_rows.append("\t".join(map(str,[num, ts, txh, h2i(lg.get("logIndex")),
                                laddr, frm, to, h2i(lg.get("data")), 0, "erc20"])))
    ch_insert("blocks","number,hash,parent_hash,timestamp,miner,gas_used,gas_limit,base_fee,tx_count,size", blk_rows)
    ch_insert("transactions","hash,block_number,tx_index,timestamp,from_addr,to_addr,value,gas,gas_price,method_id,input,status,nonce", tx_rows)
    ch_insert("logs","block_number,tx_hash,tx_index,log_index,timestamp,address,topic0,topic1,topic2,topic3,data", log_rows)
    ch_insert("token_transfers","block_number,timestamp,tx_hash,log_index,token,from_addr,to_addr,value,token_id,kind", tt_rows)
    total = len(blk_rows)+len(tx_rows)+len(log_rows)+len(tt_rows)
    ch_insert("etl_progress","chunk,done,rows", [f"{chunk}\t1\t{total}"])
    return (chunk, len(blk_rows), total)

def main():
    global TIP
    TIP = h2i(rpc([{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}])[0]["result"])
    nchunks = (TIP // CHUNK) + 1
    done = set()
    try:
        for line in ch_query("SELECT chunk FROM rh.etl_progress FINAL WHERE done=1 FORMAT TabSeparated").split("\n"):
            if line.strip(): done.add(int(line))
    except Exception: pass
    todo = [c for c in range(nchunks) if c not in done]
    print(f"TIP={TIP} chunks={nchunks} done={len(done)} todo={len(todo)} workers={WORKERS}", flush=True)
    t0=time.time(); processed=0; rows=0
    with mp.Pool(WORKERS) as pool:
        for ch, nb, tr in pool.imap_unordered(process_chunk, todo):
            processed+=1; rows+=tr
            el=time.time()-t0; bps=(processed*CHUNK)/el if el else 0
            eta=(len(todo)-processed)*CHUNK/bps/3600 if bps else 0
            print(f"[{processed}/{len(todo)}] chunk {ch} done  {bps:.0f} blk/s  rows={rows}  ETA {eta:.1f}h", flush=True)
    print(f"DONE in {(time.time()-t0)/3600:.2f}h  rows={rows}", flush=True)

if __name__=="__main__":
    main()
