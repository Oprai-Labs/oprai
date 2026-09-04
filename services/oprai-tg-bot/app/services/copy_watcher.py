"""Copy-trade watcher — real-time trade detection straight off OUR node.

Copy-trade needs SUB-SECOND latency (in memecoins one second is the difference), so
it CANNOT go through the index (~2-6s lag + the 8s alert poll). It listens to the
node's `newHeads` WebSocket stream (falls back to a 400ms head poll if the socket is
unavailable) and, for every new block, pulls ALL receipts in one call
(`eth_getBlockReceipts`) and looks for ERC20 Transfers that credit or debit a
tracked wallet. That catches:
  • direct buys and sells (tx.from == wallet),
  • bundled / relayed trades (ERC-4337 smart accounts, aggregators) where tx.from
    is a bundler and the wallet only appears in the Transfer logs.
Each hit is decoded by chain-intel's `/decode/tx/{hash}` (venue, PoolKey / curve,
tokens, amounts, USD, direction) so the copier knows exactly WHAT to replicate and
WHERE — a buy to copy, or a sell to mirror (exit).

Detection only. Execution (build+sign+submit) is the injected `on_trade` callback,
wired to the signer+gateway trade flow with the user's risk limits. Self-heals
forever: any error is logged and the loop continues."""
from __future__ import annotations

import asyncio
import json
import time

import httpx

from app.logging_config import log

# ERC20 Transfer(address,address,uint256)
_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_QUOTES = {
    "0x0000000000000000000000000000000000000000",  # ETH
    "0x0bd7d308f8e1639fab988df18a8011f41eacad73",  # WETH
    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",  # USDG
    "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34",  # USDe
}


async def _rpc(c: httpx.AsyncClient, node: str, method: str, params: list):
    r = await c.post(node, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    return (r.json() or {}).get("result")


async def _decode(c: httpx.AsyncClient, chain_intel: str, tx_hash: str, api_key: str = "") -> dict | None:
    """chain-intel's receipt decoder — one node read, sub-second."""
    try:
        r = await c.get(f"{chain_intel}/decode/tx/{tx_hash}",
                        headers={"X-Internal-Api-Key": api_key} if api_key else {})
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.warning("copy_decode_failed", tx=tx_hash[:12], error=str(e)[:120])
    return None


def _touched(receipts: list[dict], wallets: set[str]) -> dict[str, str]:
    """tx_hash → wallet for receipts whose ERC20 Transfer logs credit or debit a
    tracked wallet (buys credit, sells debit; relayed trades show up here too)."""
    hits: dict[str, str] = {}
    for rc in receipts or []:
        if rc.get("status") not in ("0x1", 1):
            continue
        for lg in rc.get("logs") or []:
            topics = lg.get("topics") or []
            if len(topics) == 3 and topics[0].lower() == _TRANSFER:
                frm, to = "0x" + topics[1][-40:].lower(), "0x" + topics[2][-40:].lower()
                w = to if to in wallets else frm if frm in wallets else None
                if w:
                    hits[rc["transactionHash"]] = w
                    break
    return hits


async def _handle_block(c: httpx.AsyncClient, node: str, chain_intel: str, bn: int,
                        get_wallets, on_trade, api_key: str, seen_at: float) -> None:
    wallets = get_wallets()
    if not wallets:
        return
    receipts = await _rpc(c, node, "eth_getBlockReceipts", [hex(bn)])
    if receipts is None:  # node without eth_getBlockReceipts → per-tx fallback
        blk = await _rpc(c, node, "eth_getBlockByNumber", [hex(bn), True])
        receipts = []
        for tx in (blk or {}).get("transactions", []):
            if tx.get("from", "").lower() in wallets:
                rc = await _rpc(c, node, "eth_getTransactionReceipt", [tx["hash"]])
                if rc:
                    receipts.append(rc)
    for tx_hash, wallet in _touched(receipts, wallets).items():
        d = await _decode(c, chain_intel, tx_hash, api_key)
        if not d or d.get("direction") not in ("buy", "sell", "swap"):
            continue
        d["wallet"] = wallet
        d["detected_after_s"] = round(time.time() - seen_at, 3)
        log.info("copy_trade_seen", wallet=wallet[:10], direction=d["direction"], block=bn,
                 spent_usd=d.get("spent_usd"), received_usd=d.get("received_usd"),
                 bought=[b.get("symbol") for b in d.get("bought", [])],
                 sold=[s.get("symbol") for s in d.get("sold", [])],
                 venues=d.get("venues"), latency_s=d["detected_after_s"])
        await on_trade(wallet, d)


async def _ws_heads(ws_url: str):
    """Yield new head numbers from the node's newHeads subscription."""
    import websockets  # optional dependency; poll fallback if missing
    async with websockets.connect(ws_url, ping_interval=20, max_size=2 ** 22) as ws:
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newHeads"]}))
        await ws.recv()  # subscription id
        while True:
            msg = json.loads(await ws.recv())
            head = (msg.get("params") or {}).get("result") or {}
            if head.get("number"):
                yield int(head["number"], 16)


async def watch_trades(node: str, chain_intel: str, get_wallets, on_trade, ws_url: str | None = None,
                       poll_ms: int = 400, max_block_span: int = 20, api_key: str = "") -> None:
    """Watch the node head; call on_trade(wallet, decoded) for every buy / sell / swap
    by a copy-tracked wallet. `get_wallets` returns the live lowercased set (so a
    user enabling/disabling copy takes effect next block). WebSocket first, poll
    fallback; never dies."""
    log.info("copy_watcher_start", node=node, ws=ws_url, poll_ms=poll_ms)
    last: int | None = None
    async with httpx.AsyncClient(timeout=8.0) as c:
        while True:
            # ── WebSocket stream ──
            if ws_url:
                try:
                    async for head in _ws_heads(ws_url):
                        seen = time.time()
                        lo = head if last is None else max(last + 1, head - max_block_span)
                        for bn in range(lo, head + 1):
                            try:
                                await _handle_block(c, node, chain_intel, bn, get_wallets, on_trade, api_key, seen)
                            except Exception as e:
                                log.warning("copy_block_error", block=bn, error=str(e)[:160])
                        last = head
                except Exception as e:
                    log.warning("copy_ws_down_polling", error=str(e)[:160])
            # ── poll fallback (also the path when no ws_url) ──
            t_end = time.time() + (30 if ws_url else 10 ** 9)   # retry the socket every 30s
            while time.time() < t_end:
                try:
                    head = int(await _rpc(c, node, "eth_blockNumber", []), 16)
                    seen = time.time()
                    if last is None:
                        last = head - 1
                    lo = max(last + 1, head - max_block_span)
                    for bn in range(lo, head + 1):
                        await _handle_block(c, node, chain_intel, bn, get_wallets, on_trade, api_key, seen)
                    last = head
                except Exception as e:
                    log.warning("copy_watcher_error", error=str(e)[:160])
                await asyncio.sleep(poll_ms / 1000)


# ── back-compat shim: the earlier buy-only API ─────────────────────────────
async def watch_buys(node: str, get_wallets, on_buy, poll_ms: int = 400, max_block_span: int = 20,
                     chain_intel: str = "http://rh-chain-intel-api:3160", ws_url: str | None = None) -> None:
    async def _on_trade(wallet: str, d: dict) -> None:
        if d.get("direction") == "buy" and d.get("bought"):
            eth = float((d.get("quote_spent") or {}).get("0x0000000000000000000000000000000000000000") or 0)
            await on_buy(wallet, d["bought"][0]["token"], eth, d["tx"])
    await watch_trades(node, chain_intel, get_wallets, _on_trade, ws_url=ws_url,
                       poll_ms=poll_ms, max_block_span=max_block_span)
