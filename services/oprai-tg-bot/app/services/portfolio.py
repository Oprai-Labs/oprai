"""Read-only balances on Robinhood Chain, straight from our node.

Reads go to settings.robinhood_rpc() — OUR self-hosted Nitro full node in prod —
so a balance is the current on-chain state with no indexer lag. Actions still
flow through the gateway with the on-behalf JWT (see auth.py).
"""

from __future__ import annotations

import httpx

from app.config import settings
from app.services import wallet as wallet_svc

WEI_PER_ETH = 10**18


class PortfolioError(RuntimeError):
    pass


async def _rpc(method: str, params: list) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(settings.robinhood_rpc(), json=payload)
    except httpx.HTTPError as e:
        raise PortfolioError(f"rpc unreachable: {e}") from e
    if r.status_code != 200:
        raise PortfolioError(f"rpc HTTP {r.status_code}")
    body = r.json()
    if "error" in body:
        raise PortfolioError(f"rpc error: {body['error']}")
    return body


async def native_balance(telegram_id: int) -> dict:
    """Native ETH balance on Robinhood Chain for the user's wallet."""
    addr = await wallet_svc.wallet_address(telegram_id)
    body = await _rpc("eth_getBalance", [addr, "latest"])
    wei = int(body.get("result", "0x0"), 16)
    return {"address": addr, "wei": wei, "eth": wei / WEI_PER_ETH}
