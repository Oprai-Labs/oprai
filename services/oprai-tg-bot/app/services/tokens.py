"""Token registry for Robinhood Chain: "NVDA" / "nvidia" / 0x… -> contract.

Seeded from Robinhood's official stock-token registry (no auth, chainId 4663)
plus a few well-known base assets. Decimals are read from the chain — a wrong
decimals figure silently sends 10^n times the intended amount, so it is never
assumed.
"""

from __future__ import annotations

import asyncio

import httpx

from app.db import pool
from app.logging_config import log
from app.services import evm

ASSETS_URL = "https://api.robinhood.com/rhj/assets"
ROBINHOOD_CHAIN_ID = 4663

# ERC-20 view selectors
SEL_DECIMALS = "0x313ce567"
SEL_SYMBOL = "0x95d89b41"
SEL_BALANCE_OF = "0x70a08231"

# Non-stock assets people actually hold on Robinhood Chain. Addresses are
# verified on-chain at sync (symbol() must answer) before they are stored.
BASE_ASSET_ADDRESSES = [
    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",  # USDG — 6 decimals, not 18
    "0x0bd7d308f8e1639fab988df18a8011f41eacad73",  # WETH
]


class TokenError(RuntimeError):
    pass


# ── chain reads ─────────────────────────────────────────────────────────────
async def _call(to: str, data: str) -> str | None:
    try:
        return await evm.rpc("eth_call", [{"to": to, "data": data}, "latest"])
    except evm.EvmError:
        return None


async def read_decimals(address: str) -> int | None:
    res = await _call(address, SEL_DECIMALS)
    if not res or res == "0x":
        return None
    try:
        return int(res, 16)
    except ValueError:
        return None


async def read_symbol(address: str) -> str | None:
    """Handles both dynamic-string and legacy bytes32 symbol() returns."""
    res = await _call(address, SEL_SYMBOL)
    if not res or res == "0x":
        return None
    raw = bytes.fromhex(res[2:])
    if len(raw) == 32:  # legacy bytes32
        return raw.rstrip(b"\x00").decode("utf-8", "ignore").strip() or None
    if len(raw) >= 64:  # offset + length + data
        length = int.from_bytes(raw[32:64], "big")
        return raw[64 : 64 + length].decode("utf-8", "ignore").strip() or None
    return None


async def token_balance(token: str, holder: str) -> int:
    data = SEL_BALANCE_OF + holder.lower().removeprefix("0x").rjust(64, "0")
    res = await _call(token, data)
    return int(res, 16) if res and res != "0x" else 0


def _decode_symbol(res: str | None) -> str | None:
    if not res or res == "0x":
        return None
    raw = bytes.fromhex(res[2:])
    if len(raw) == 32:  # legacy bytes32
        return raw.rstrip(b"\x00").decode("utf-8", "ignore").strip() or None
    if len(raw) >= 64:  # offset + length + data
        length = int.from_bytes(raw[32:64], "big")
        return raw[64 : 64 + length].decode("utf-8", "ignore").strip() or None
    return None


async def _batch_decimals(addresses: list[str], chunk: int = 20) -> list[int | None]:
    out: list[int | None] = []
    for i in range(0, len(addresses), chunk):
        part = addresses[i : i + chunk]
        results = await evm.rpc_batch(
            [("eth_call", [{"to": a, "data": SEL_DECIMALS}, "latest"]) for a in part]
        )
        for res in results:
            try:
                out.append(int(res, 16) if res and res != "0x" else None)
            except (TypeError, ValueError):
                out.append(None)
        await asyncio.sleep(0.25)
    return out


async def _batch_symbols(addresses: list[str], chunk: int = 20) -> list[str | None]:
    out: list[str | None] = []
    for i in range(0, len(addresses), chunk):
        part = addresses[i : i + chunk]
        results = await evm.rpc_batch(
            [("eth_call", [{"to": a, "data": SEL_SYMBOL}, "latest"]) for a in part]
        )
        out.extend(_decode_symbol(res) for res in results)
        await asyncio.sleep(0.25)
    return out


# ── sync ────────────────────────────────────────────────────────────────────
async def sync_registry() -> dict[str, int]:
    """Refresh the registry from Robinhood's asset list + base assets."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(ASSETS_URL)
        r.raise_for_status()
        assets = r.json().get("assets", [])
    except (httpx.HTTPError, ValueError) as e:
        raise TokenError(f"stock-token registry unreachable: {e}") from e

    # Collect candidates first, then read decimals for all of them in batches —
    # 194 sequential round-trips to the RPC is minutes; batched it is seconds.
    candidates: list[tuple[str, str, str, bool]] = []  # addr, symbol, name, is_stock
    for a in assets:
        if a.get("status") != "ASSET_STATUS_ACTIVE":
            continue
        addr = next(
            (
                d.get("contractAddress")
                for d in a.get("deployments", [])
                if d.get("chainId") == ROBINHOOD_CHAIN_ID
            ),
            None,
        )
        symbol = (a.get("tokenSymbol") or "").strip()
        if addr and symbol:
            candidates.append((addr, symbol, (a.get("tokenName") or "").strip(), True))

    base_syms = await _batch_symbols(BASE_ASSET_ADDRESSES)
    for addr, sym in zip(BASE_ASSET_ADDRESSES, base_syms):
        if sym:
            candidates.append((addr, sym, sym, False))
        else:
            log.warning("base_asset_unverified", address=addr)

    decimals_list = await _batch_decimals([c[0] for c in candidates])

    rows: list[tuple] = []
    stocks = base = 0
    for (addr, symbol, name, is_stock), dec in zip(candidates, decimals_list):
        if dec is None:
            log.warning("token_decimals_unreadable", address=addr, symbol=symbol)
            continue  # never guess decimals
        rows.append((addr, symbol, name, dec, is_stock))
        if is_stock:
            stocks += 1
        else:
            base += 1

    if not rows:
        raise TokenError("registry sync produced no tokens")

    async with pool().acquire() as con:
        async with con.transaction():
            for addr, sym, name, dec, is_stock in rows:
                await con.execute(
                    """
                    INSERT INTO tg_token_registry (address, symbol, name, decimals, is_stock)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (address) DO UPDATE SET
                        symbol = EXCLUDED.symbol, name = EXCLUDED.name,
                        decimals = EXCLUDED.decimals, is_stock = EXCLUDED.is_stock,
                        updated_at = now()
                    """,
                    addr, sym, name, dec, is_stock,
                )
    log.info("token_registry_synced", stocks=stocks, base=base)
    return {"stocks": stocks, "base": base, "total": len(rows)}


async def registry_size() -> int:
    return await pool().fetchval("SELECT count(*) FROM tg_token_registry")


# ── resolution ──────────────────────────────────────────────────────────────
async def resolve(query: str) -> list[dict]:
    """Resolve a user's token reference to candidates, best match first.

    Accepts a 0x address (read live from chain if unknown), an exact symbol
    ("NVDA", "$nvda"), or part of a name ("nvidia", "tesla").
    """
    q = query.strip().lstrip("$")
    if not q:
        return []

    if q.startswith("0x") and len(q) == 42:
        row = await pool().fetchrow(
            "SELECT address, symbol, name, decimals FROM tg_token_registry "
            "WHERE lower(address) = lower($1)",
            q,
        )
        if row:
            return [dict(row)]
        symbol = await read_symbol(q)
        decimals = await read_decimals(q)
        if decimals is None:
            return []
        return [{"address": q, "symbol": symbol or q[:8], "name": symbol or "", "decimals": decimals}]

    rows = await pool().fetch(
        """
        SELECT address, symbol, name, decimals,
               CASE WHEN upper(symbol) = upper($1) THEN 0
                    WHEN upper(symbol) LIKE upper($1) || '%' THEN 1
                    ELSE 2 END AS rank
        FROM tg_token_registry
        WHERE upper(symbol) = upper($1)
           OR upper(symbol) LIKE upper($1) || '%'
           OR lower(name) LIKE '%' || lower($1) || '%'
        ORDER BY rank, is_stock DESC, symbol
        LIMIT 8
        """,
        q,
    )
    return [dict(r) for r in rows]
