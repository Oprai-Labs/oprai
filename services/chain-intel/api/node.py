"""Node RPC helper — eth_call the Robinhood-Chain full node for live curve/pool
state (honeypot / exit checks). The API is attached to the node's docker network."""
from __future__ import annotations
import os
import httpx

NODE = os.environ.get("NODE_RPC", "http://rh-nitro:8547")
PONS_V2_FACTORY = "0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e"


async def eth_call(to: str, data: str) -> str:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
               "params": [{"to": to, "data": data}, "latest"]}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(NODE, json=payload)
        return (r.json() or {}).get("result") or "0x"


async def contract_addresses(addresses: list[str]) -> set[str]:
    """The subset of `addresses` that are CONTRACTS (have bytecode) — one batched
    eth_getCode call. Used to drop LP pools / routers / other contracts from
    WALLET-concentration stats, so a pool holding liquidity isn't miscounted as a
    whale hoarding supply. Fails open (returns empty) so a node hiccup never
    corrupts the report."""
    if not addresses:
        return set()
    batch = [{"jsonrpc": "2.0", "id": i, "method": "eth_getCode", "params": [a, "latest"]}
             for i, a in enumerate(addresses)]
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(NODE, json=batch)
            res = r.json() or []
    except Exception:
        return set()
    by_id = {item.get("id"): item.get("result") for item in res if isinstance(item, dict)}
    return {a for i, a in enumerate(addresses) if (by_id.get(i) or "0x") not in ("0x", "", None)}


def _w(hx: str, i: int) -> int:
    hx = hx[2:] if hx.startswith("0x") else hx
    s = hx[i * 64:(i + 1) * 64]
    return int(s, 16) if s else 0


def _addr(hx: str, i: int) -> str:
    hx = hx[2:] if hx.startswith("0x") else hx
    return "0x" + hx[i * 64 + 24:(i + 1) * 64]


def _enc_addr(a: str) -> str:
    return a.lower().replace("0x", "").rjust(64, "0")


async def pons_curve_state(token: str) -> dict | None:
    """If `token` is a Pons V2 launch, return live curve state; else None."""
    gl = await eth_call(PONS_V2_FACTORY, "0x3cf28b5a" + _enc_addr(token))  # getLaunchedToken
    if _w(gl, 14) == 0:
        return None
    curve = _addr(gl, 1)
    grad_threshold = _w(gl, 5) / 1e18
    res = await eth_call(curve, "0x0902f1ac")  # getReserves -> (quote, token)
    return {
        "curve": curve,
        "quote_reserve": _w(res, 0) / 1e18,
        "token_reserve": _w(res, 1) / 1e18,
        "real_quote": _w(await eth_call(curve, "0x4f1f58fd"), 0) / 1e18,  # realQuoteReserve
        "graduated": _w(await eth_call(curve, "0xe7c2b772"), 0) != 0,     # graduated()
        "graduation_threshold": grad_threshold,
        "fee_bps": _w(await eth_call(curve, "0x24a9d853"), 0),            # feeBps
        "tax_bps": _w(await eth_call(curve, "0xc1bb8901"), 0),            # creatorTaxBps
    }
