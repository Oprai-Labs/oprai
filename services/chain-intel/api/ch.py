"""ClickHouse HTTP client for the chain-intel API. Read-only, async, JSON rows."""
from __future__ import annotations

import os
import re
from typing import Any

import httpx

CH_URL = os.environ.get("CH_URL", "http://127.0.0.1:8123/")
CH_USER = os.environ.get("CH_USER", "oprai")
CH_PASS = os.environ.get("CH_PASS", "")
CH_DB = os.environ.get("CH_DB", "rh")

_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

# Robinhood-Chain quote asset (Global Dollar) — the $1 price anchor.
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"


def addr(a: str) -> str:
    """Validate + lowercase an EVM address, or raise. Prevents SQL injection —
    every address that reaches a query goes through here."""
    a = (a or "").strip().lower()
    if not _ADDR_RE.match(a):
        raise ValueError(f"invalid address: {a!r}")
    return a


def is_addr(a: str) -> bool:
    return bool(_ADDR_RE.match((a or "").strip().lower()))


async def q(sql: str, *, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Run a read-only query, return rows as dicts (JSONEachRow)."""
    body = sql.strip().rstrip(";") + "\nFORMAT JSONEachRow"
    params = {"user": CH_USER, "password": CH_PASS, "database": CH_DB,
              "readonly": "1", "max_execution_time": str(int(timeout))}
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(CH_URL, params=params, content=body.encode())
        r.raise_for_status()
        out = []
        for line in r.text.splitlines():
            if line.strip():
                import json
                out.append(json.loads(line))
        return out


async def one(sql: str, **kw) -> dict[str, Any]:
    rows = await q(sql, **kw)
    return rows[0] if rows else {}


async def scalar(sql: str, **kw) -> Any:
    row = await one(sql, **kw)
    return next(iter(row.values()), None) if row else None


async def table_exists(name: str) -> bool:
    try:
        return bool(await scalar(f"SELECT 1 FROM system.tables WHERE database='{CH_DB}' AND name='{name}'"))
    except Exception:
        return False
