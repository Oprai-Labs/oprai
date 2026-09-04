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


# ── holdings ────────────────────────────────────────────────────────────────
# balanceOf(address)
SEL_BALANCE_OF = "0x70a08231"
# One eth_call per registered token would be hundreds of round trips; the node
# takes them batched, so the whole wallet is read in a handful.
BALANCE_CHUNK = 40

# Held by almost everyone and possibly from before the watcher existed, so
# they are always checked even when nothing was recorded for this wallet.
BASE_ASSETS = (
    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",  # USDG
    "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34",  # USDe
    "0x0bd7d308f8e1639fab988df18a8011f41eacad73",  # WETH
    "0xd98e1e5a25702930b2fc92c15f3fef6d2987b5ac",  # OPRAI
)


async def token_holdings(wallet: str, telegram_id: int | None = None) -> list[dict]:
    """Every token this wallet actually holds.

    Reading balances for the whole registry was ~200 contract calls per
    request — slow enough to look broken, and enough for a public node to
    rate-limit. So we read only what this wallet has been seen to receive
    (recorded by the deposit watcher, which sees swap outputs too), plus the
    chain's base assets, which a wallet can hold from before we were watching.

    A token the registry doesn't know is skipped: without its decimals a
    balance is a number that means nothing.
    """
    from app.db import pool
    from app.services import evm

    rows = await pool().fetch(
        """
        SELECT r.address, r.symbol, r.decimals, r.is_stock
          FROM tg_token_registry r
         WHERE lower(r.address) = ANY($1::text[])
            OR ($2::bigint IS NOT NULL AND lower(r.address) IN (
                    SELECT address FROM tg_wallet_tokens WHERE telegram_id = $2
               ))
        """,
        [a.lower() for a in BASE_ASSETS],
        telegram_id,
    )
    if not rows:
        return []

    data = SEL_BALANCE_OF + wallet.lower().removeprefix("0x").rjust(64, "0")
    held: list[dict] = []
    for i in range(0, len(rows), BALANCE_CHUNK):
        chunk = rows[i:i + BALANCE_CHUNK]
        try:
            results = await evm.rpc_batch(
                [("eth_call", [{"to": r["address"], "data": data}, "latest"])
                 for r in chunk]
            )
        except evm.EvmError:
            # A rate-limited chunk costs its own rows, not the whole answer.
            continue
        for row, raw in zip(chunk, results):
            if raw in (None, "0x"):
                continue  # a failed read is not a zero balance — say nothing
            amount = evm.to_int(raw)
            if amount <= 0:
                continue
            held.append({
                "address": row["address"],
                "symbol": row["symbol"],
                "decimals": row["decimals"],
                "is_stock": row["is_stock"],
                "amount": amount,
                "display": _units(amount, row["decimals"]),
            })

    held.sort(key=lambda h: (not h["is_stock"], h["symbol"]))
    return held


def _units(amount: int, decimals: int) -> str:
    from decimal import Decimal

    value = Decimal(amount) / (10 ** int(decimals))
    text = f"{value:,.6f}".rstrip("0").rstrip(".")
    return text or "0"
