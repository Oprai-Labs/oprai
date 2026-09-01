"""Read-only balances via direct chain RPC.

The gateway's /rpc proxy is browser-origin-gated and there is no non-browser
balance endpoint, so the bot (a trusted backend, co-located with the gateway)
queries the chain RPC directly — exactly what the gateway does server-side.
Actions still flow through the gateway with the on-behalf JWT (see auth.py);
this module is only for read-only native balances.
"""

from __future__ import annotations

import httpx

from app.config import settings
from app.services import wallet as wallet_svc

LAMPORTS_PER_SOL = 1_000_000_000
WEI_PER_ETH = 10**18


class PortfolioError(RuntimeError):
    pass


async def _rpc(url: str, method: str, params: list) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(url, json=payload)
    except httpx.HTTPError as e:
        raise PortfolioError(f"rpc unreachable: {e}") from e
    if r.status_code != 200:
        raise PortfolioError(f"rpc HTTP {r.status_code}")
    body = r.json()
    if "error" in body:
        raise PortfolioError(f"rpc error: {body['error']}")
    return body


async def solana_balance(telegram_id: int) -> dict:
    """Native SOL balance for the user's Solana wallet."""
    w = await wallet_svc.get_or_create_wallet(telegram_id, "solana")
    addr = w["address"]
    body = await _rpc(settings.SOLANA_RPC, "getBalance", [addr])
    lamports = int(body.get("result", {}).get("value", 0))
    return {"address": addr, "lamports": lamports, "sol": lamports / LAMPORTS_PER_SOL}


async def evm_native_balance(telegram_id: int) -> dict:
    """Native balance for the user's EVM wallet (requires OPRAI_TG_EVM_RPC)."""
    if not settings.OPRAI_TG_EVM_RPC:
        raise PortfolioError("EVM RPC not configured")
    w = await wallet_svc.get_or_create_wallet(telegram_id, "evm")
    addr = w["address"]
    body = await _rpc(settings.OPRAI_TG_EVM_RPC, "eth_getBalance", [addr, "latest"])
    wei = int(body.get("result", "0x0"), 16)
    return {"address": addr, "wei": wei, "eth": wei / WEI_PER_ETH}
