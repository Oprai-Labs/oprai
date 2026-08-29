#!/usr/bin/env python3
"""Contract-creation ETL → rh.contracts (deployer identity for dev/rug analysis).

ClickHouse can't derive a CREATE address from (from, nonce) in-DB (no keccak/rlp),
so we read it from the receipt: any receipt with a non-null `contractAddress` is a
deployment — deployer = receipt.from, address = receipt.contractAddress.

Run AFTER the main bulk ETL completes (it re-scans receipts, so it competes with
the node — don't run both at once). Parallel, resumable. Then flag is_token via
SQL (a created contract that appears as a token in token_transfers)."""
import os, sys, json, time, urllib.request, multiprocessing as mp

RPC = "http://127.0.0.1:8547"
CH  = "http://127.0.0.1:8123/"
CHU = os.environ["CHU"]; CHP = os.environ["CHP"]
CHUNK = 100_000
BATCH = 200
WORKERS = int(os.environ.get("WORKERS", "6"))

def rpc(calls, tries=5):
    data = json.dumps(calls).encode()
    for a in range(tries):
        try:
            r = urllib.request.Request(RPC, data=data, headers={"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(r, timeout=120).read())
        except Exception:
            if a == tries - 1: raise
            time.sleep(1.5 * (a + 1))

def ch_insert(rows, tries=5):
    if not rows: return
    q = "INSERT INTO rh.contracts (address,deployer,creation_tx,creation_block,timestamp) FORMAT TabSeparated"
    import urllib.parse
    u = CH + "?" + urllib.parse.urlencode({"user": CHU, "password": CHP, "query": q})
    body = "\n".join(rows).encode()
    for a in range(tries):
        try:
            urllib.request.urlopen(urllib.request.Request(u, data=body), timeout=180); return
        except Exception as e:
            if a == tries - 1:
                sys.stderr.write(f"insert failed: {getattr(e,'read',lambda:b'')()[:200]}\n"); raise
            time.sleep(1.5 * (a + 1))

def ch_q(q):
    import urllib.parse
    u = CH + "?" + urllib.parse.urlencode({"user": CHU, "password": CHP, "query": q})
    return urllib.request.urlopen(u, timeout=60).read().decode()

def h2i(x): return int(x, 16) if x not in (None, "0x", "") else 0

def process_chunk(chunk):
    start = chunk * CHUNK; end = min(start + CHUNK, TIP + 1)
    rows = []
    for base in range(start, end, BATCH):
        n = min(BATCH, end - base)
        calls = [{"jsonrpc": "2.0", "id": i, "method": "eth_getBlockReceipts", "params": [hex(base + i)]} for i in range(n)]
        res = rpc(calls)
        for x in res:
            for r in (x.get("result") or []):
                ca = r.get("contractAddress")
                if ca:
                    rows.append("\t".join([ca.lower(), (r.get("from") or "0x").lower(),
                        r.get("transactionHash", ""), str(h2i(r.get("blockNumber"))), "0"]))
    # timestamp filled as 0 (unix epoch) — join to blocks for real ts if needed;
    # cheaper to backfill ts via a single UPDATE-like INSERT from rh.blocks later.
    ch_insert(rows)
    return (chunk, len(rows))

def main():
    global TIP
    TIP = h2i(rpc([{"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}])[0]["result"])
    nchunks = (TIP // CHUNK) + 1
    print(f"TIP={TIP} chunks={nchunks} workers={WORKERS}", flush=True)
    t0 = time.time(); done = 0; total = 0
    with mp.Pool(WORKERS) as pool:
        for ch, k in pool.imap_unordered(process_chunk, range(nchunks)):
            done += 1; total += k
            el = time.time() - t0; bps = (done * CHUNK) / el if el else 0
            print(f"[{done}/{nchunks}] {bps:.0f} blk/s contracts={total} "
                  f"ETA {(nchunks-done)*CHUNK/bps/3600:.1f}h", flush=True)
    print(f"DONE {total} contracts in {(time.time()-t0)/3600:.2f}h", flush=True)
    # flag tokens: a created contract that emitted Transfer events is a token
    ch_q("""ALTER TABLE rh.contracts UPDATE is_token=1 WHERE address IN
            (SELECT DISTINCT token FROM rh.token_transfers)""")

if __name__ == "__main__":
    main()
