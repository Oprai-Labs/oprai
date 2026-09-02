"""Postgres store for copy-trade: per-(user, leader) config, the daily-cap ledger,
fills. Implements the `store` seam CopyEngine expects (copiers_of / spent_today_usd /
record_fill) plus handler CRUD. Rows live in tg_schema (sql/schema.sql)."""
from __future__ import annotations

from app.db import pool
from app.services.copy_engine import CopyConfig


def _cfg(r) -> CopyConfig:
    return CopyConfig(
        enabled=bool(r["enabled"]),
        mode=r["mode"],
        amount_eth=float(r["amount_eth"]),
        max_per_trade_eth=float(r["max_per_trade_eth"]),
        min_per_trade_eth=float(r["min_per_trade_eth"]),
        daily_cap_usd=float(r["daily_cap_usd"]),
    )


class CopyStore:
    # ── engine reads ───────────────────────────────────────────────────────
    async def copiers_of(self, leader: str) -> list[dict]:
        rows = await pool().fetch(
            "SELECT telegram_id, enabled, mode, amount_eth, max_per_trade_eth, "
            "min_per_trade_eth, daily_cap_usd FROM tg_copy_subs "
            "WHERE leader=$1 AND enabled", leader.lower())
        return [{"telegram_id": r["telegram_id"], "config": _cfg(r)} for r in rows]

    async def all_leaders(self) -> set[str]:
        rows = await pool().fetch(
            "SELECT DISTINCT leader FROM tg_copy_subs WHERE enabled")
        return {r["leader"] for r in rows}

    async def spent_today_usd(self, uid: int) -> float:
        v = await pool().fetchval(
            "SELECT COALESCE(SUM(usd),0) FROM tg_copy_fills "
            "WHERE telegram_id=$1 AND status<>'failed' "
            "AND created_at > now() - interval '24 hours'", uid)
        return float(v or 0)

    async def record_fill(self, uid: int, leader: str, token: str, amount_eth: float,
                          usd: float, leader_tx: str, our_tx: str | None = None,
                          status: str = "sent") -> None:
        await pool().execute(
            "INSERT INTO tg_copy_fills (telegram_id, leader, token, amount_eth, usd, "
            "leader_tx, our_tx, status) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
            uid, leader.lower(), token.lower(), amount_eth, usd, leader_tx, our_tx, status)

    # ── handler CRUD ───────────────────────────────────────────────────────
    async def set_copy(self, uid: int, leader: str, enabled: bool = True,
                       amount_eth: float | None = None, mode: str | None = None,
                       max_per_trade_eth: float | None = None,
                       daily_cap_usd: float | None = None) -> None:
        await pool().execute(
            "INSERT INTO tg_copy_subs (telegram_id, leader, enabled, amount_eth, mode, "
            "max_per_trade_eth, daily_cap_usd) "
            "VALUES ($1,$2,$3,COALESCE($4,0.01),COALESCE($5,'fixed'),COALESCE($6,0.05),COALESCE($7,100)) "
            "ON CONFLICT (telegram_id, leader) DO UPDATE SET "
            "  enabled=EXCLUDED.enabled, "
            "  amount_eth=COALESCE($4, tg_copy_subs.amount_eth), "
            "  mode=COALESCE($5, tg_copy_subs.mode), "
            "  max_per_trade_eth=COALESCE($6, tg_copy_subs.max_per_trade_eth), "
            "  daily_cap_usd=COALESCE($7, tg_copy_subs.daily_cap_usd)",
            uid, leader.lower(), enabled, amount_eth, mode, max_per_trade_eth, daily_cap_usd)

    async def list_copies(self, uid: int) -> list[dict]:
        rows = await pool().fetch(
            "SELECT leader, enabled, mode, amount_eth, max_per_trade_eth, daily_cap_usd "
            "FROM tg_copy_subs WHERE telegram_id=$1 ORDER BY created_at DESC", uid)
        return [dict(r) for r in rows]
