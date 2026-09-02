"""Node RPC helper — eth_call the Robinhood-Chain full node for live curve/pool
state (honeypot / exit checks). The API is attached to the node's docker network."""
from __future__ import annotations
import json
import os
import httpx

NODE = os.environ.get("NODE_RPC", "http://rh-nitro:8547")
# pools.trade (Uniswap's shared launch infra on Robinhood Chain) tRPC — the
# authoritative source for its OWN launches: it returns the launchpad id and the
# REAL creator EOA. Partner launchpads (Pons/Noxa/LONG) come back `unsupported`
# and are identified on-chain via their own factories instead.
POOLS_TRADE_TRPC = "https://pools.trade/api/trpc"
PONS_V2_FACTORY = "0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e"
PONS_V1_FACTORY = "0xA5aAb3F0c6EeadF30Ef1D3Eb997108E976351feB"

# Known Robinhood-Chain launchpads, each with a detector: (name, factory, selector,
# word_index) where factory.<selector>(token) returns a launch record whose
# word[word_index] is non-zero when the launchpad owns this token. Extensible — add
# a row per launchpad (pools.trade / Noxa / LONG / …) once its factory + read method
# are known.
_LAUNCHPADS = [
    ("Pons", PONS_V2_FACTORY, "0x3cf28b5a", 14),  # getLaunchedToken(token)
    ("Pons", PONS_V1_FACTORY, "0x3cf28b5a", 14),
]


async def detect_launchpad(token: str) -> str | None:
    """Identify which known launchpad minted `token`, by asking each launchpad's
    factory whether it owns the launch. Returns the launchpad name (e.g. 'Pons')
    or None if it isn't one we recognise. Fails open per-launchpad (a node hiccup
    on one never blocks the others)."""
    for name, factory, selector, wi in _LAUNCHPADS:
        try:
            gl = await eth_call(factory, selector + _enc_addr(token))
            if len(gl) > 2 and _w(gl, wi) != 0:
                return name
        except Exception:
            continue
    return None


async def pools_trade_launch(token: str) -> dict | None:
    """If `token` is a genuine pools.trade launch (Uniswap bonding-curve / CCA on
    Robinhood Chain), return {'launchpad': 'pools.trade', 'creator': <addr|None>}.
    Returns None for anything pools.trade doesn't natively serve — a made-up
    address, a token traded elsewhere, or a PARTNER launchpad's token (those come
    back `unsupported: true`, e.g. a Pons launch, and are named on-chain instead).
    Fails open (None) so a pools.trade API hiccup never blocks the on-chain path."""
    q = json.dumps({"tokenAddress": token})
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            r = await c.get(f"{POOLS_TRADE_TRPC}/curve.getLaunchByAddress",
                            params={"input": q}, headers={"accept": "application/json"})
            data = ((r.json() or {}).get("result") or {}).get("data") or {}
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("tokenSymbol"):
        return None
    if data.get("unsupported") is True:
        return None
    img = data.get("imageUrl") or ""
    if img.startswith("ipfs://"):
        img = "https://ipfs.pools.trade/ipfs/" + img[7:]
    elif img and not img.startswith("http"):
        img = "https://ipfs.pools.trade/ipfs/" + img
    creator = data.get("creatorAddress")
    if isinstance(creator, str) and creator.lower().startswith("0x"):
        creator = creator.lower()
    else:
        creator = None
    return {"launchpad": "pools.trade", "creator": creator, "image": img or None,
            "symbol": data.get("tokenSymbol"), "name": data.get("tokenName")}


async def eth_call(to: str, data: str) -> str:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
               "params": [{"to": to, "data": data}, "latest"]}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(NODE, json=payload)
        return (r.json() or {}).get("result") or "0x"


def _decode_str(hx: str) -> str | None:
    """Decode an ERC20 symbol()/name() eth_call return — handles both the standard
    ABI dynamic string (offset+length+utf8) and the legacy bytes32 right-padded form.
    Returns None if empty/garbage."""
    hx = (hx or "").removeprefix("0x")
    if not hx or int(hx or "0", 16) == 0:
        return None
    try:
        if len(hx) >= 128:  # dynamic string: [offset][length][data]
            length = int(hx[64:128], 16)
            if 0 < length <= 256:
                raw = bytes.fromhex(hx[128:128 + length * 2])
                s = raw.decode("utf-8", "ignore").strip("\x00").strip()
                if s:
                    return s
        # legacy bytes32
        raw = bytes.fromhex(hx[:64])
        s = raw.decode("utf-8", "ignore").strip("\x00").strip()
        return s or None
    except Exception:
        return None


async def resolve_symbols(tokens: list[str]) -> dict[str, str]:
    """Batched ERC20 symbol() (selector 0x95d89b41) for tokens whose symbol isn't in
    the decode table (launchpad/factory tokens). One JSON-RPC batch. Fails open."""
    tokens = [t for t in tokens if t]
    if not tokens:
        return {}
    batch = [{"jsonrpc": "2.0", "id": i, "method": "eth_call",
              "params": [{"to": t, "data": "0x95d89b41"}, "latest"]}
             for i, t in enumerate(tokens)]
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(NODE, json=batch)
            res = r.json() or []
    except Exception:
        return {}
    by_id = {item.get("id"): item.get("result") for item in res if isinstance(item, dict)}
    out: dict[str, str] = {}
    for i, t in enumerate(tokens):
        sym = _decode_str(by_id.get(i) or "")
        if sym:
            out[t] = sym
    return out


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
