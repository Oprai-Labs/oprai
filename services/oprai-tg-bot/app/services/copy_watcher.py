"""Copy-trade watcher — real-time buy detection straight off OUR node.

Copy-trade needs SUB-SECOND latency (in memecoins one second is the difference), so
it CANNOT go through the index (~4s lag + the 8s alert poll). Instead it watches the
node head directly (fed by the sequencer feed, ~1s behind wall-clock) at ~400ms and,
for every new block, spots buys by copy-tracked wallets and resolves the bought token
from the receipt — so a copy fires ~1-2s behind the leader.

Detection only. Execution (build+sign+submit the same buy) is the injected `on_buy`
callback, wired to the signer+gateway trade flow with the user's risk limits."""
from __future__ import annotations

import asyncio

import httpx

from app.logging_config import log

_ZERO_TOPIC = "0x0000000000000000000000000000000000000000000000000000000000000000"
# ERC20 Transfer(address,address,uint256)
_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


async def _rpc(c: httpx.AsyncClient, node: str, method: str, params: list):
    r = await c.post(node, json={"jsonrpc": "2.0", "id": 1,
                                 "method": method, "params": params})
    return (r.json() or {}).get("result")


async def resolve_buy(c: httpx.AsyncClient, node: str, tx: dict) -> tuple[str, float] | None:
    """Given a tx that spent ETH, return (bought_token, eth_spent) if the sender
    RECEIVED an ERC20 in it (a buy), else None. The bought token is the ERC20 whose
    Transfer log credits the sender."""
    eth = int(tx.get("value", "0x0"), 16) / 1e18
    if eth <= 0:
        return None
    rc = await _rpc(c, node, "eth_getTransactionReceipt", [tx["hash"]])
    if not rc or rc.get("status") not in ("0x1", 1) or not rc.get("logs"):
        return None
    sender = tx["from"].lower()
    for lg in rc["logs"]:
        topics = lg.get("topics") or []
        if len(topics) == 3 and topics[0].lower() == _TRANSFER:
            to = "0x" + topics[2][-40:].lower()
            if to == sender:
                return lg["address"].lower(), eth
    return None


async def watch_buys(node: str, get_wallets, on_buy, poll_ms: int = 400,
                     max_block_span: int = 20, fallback: str | None = None) -> None:
    """Watch the node head; call on_buy(wallet, token, eth_spent, tx_hash) for each
    buy by a copy-tracked wallet. `get_wallets` returns the live lowercased set (so
    a user enabling/disabling copy takes effect next block). Self-heals forever.

    `fallback` is used while the primary node isn't answering. Our own node
    stops serving RPC for an hour at a time while it prunes, and at a 400ms
    poll that produced nine thousand identical warnings an hour and no copy
    trades — the loudest possible way to be silently broken.
    """
    log.info("copy_watcher_start", node=node, poll_ms=poll_ms)
    last: int | None = None
    nodes = [node] + ([fallback] if fallback and fallback != node else [])
    index = 0
    quiet_until = 0.0
    async with httpx.AsyncClient(timeout=8.0) as c:
        while True:
            node = nodes[index]
            try:
                head = int(await _rpc(c, node, "eth_blockNumber", []), 16)
                if last is None:
                    last = head - 1
                # never scan a huge backlog (a stall/restart shouldn't replay hours)
                lo = max(last + 1, head - max_block_span)
                for bn in range(lo, head + 1):
                    wallets = get_wallets()
                    if not wallets:
                        continue
                    blk = await _rpc(c, node, "eth_getBlockByNumber", [hex(bn), True])
                    for tx in (blk or {}).get("transactions", []):
                        if tx.get("from", "").lower() in wallets \
                                and int(tx.get("value", "0x0"), 16) > 0:
                            hit = await resolve_buy(c, node, tx)
                            if hit:
                                token, eth = hit
                                await on_buy(tx["from"].lower(), token, eth, tx["hash"])
                last = head
                index = 0  # the primary answered; go back to it
            except Exception as e:  # never die
                if len(nodes) > 1:
                    index = (index + 1) % len(nodes)
                # A failing node at a 400ms poll writes a warning twice a
                # second. Say it once a minute: the same line nine thousand
                # times is not more information, it is less.
                now = asyncio.get_event_loop().time()
                if now >= quiet_until:
                    log.warning("copy_watcher_error", error=str(e)[:160],
                                node=node, next_node=nodes[index])
                    quiet_until = now + 60
                # And back off, rather than hammering something that is down.
                await asyncio.sleep(2.0)
            await asyncio.sleep(poll_ms / 1000)
