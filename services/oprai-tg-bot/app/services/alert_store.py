"""Postgres-backed subscription store for the alpha alert worker.

Implements the `store` seam `alert_worker` expects (discovery_subs / tracked_wallets
/ cursors / dedup) plus the CRUD the handlers call (add/remove/list/toggle). All
rows live in tg_schema (see sql/schema.sql); the pool is the bot's shared db.pool()."""
from __future__ import annotations

from app.db import pool


class AlertStore:
    # ── worker reads ───────────────────────────────────────────────────────
    async def discovery_subs(self) -> list[dict]:
        rows = await pool().fetch(
            "SELECT telegram_id, min_smart, new_only, cursor_block "
            "FROM tg_alert_subs WHERE smart_alerts = TRUE")
        return [dict(r) for r in rows]

    async def tracked_wallets(self) -> list[dict]:
        rows = await pool().fetch(
            "SELECT id, telegram_id, address, label, cursor_block FROM tg_tracked_wallets")
        return [dict(r) for r in rows]

    async def was_sent_recently(self, uid: int, token: str, kind: str,
                                cooldown_min: int) -> bool:
        row = await pool().fetchrow(
            "SELECT sent_at FROM tg_alert_sent "
            "WHERE telegram_id=$1 AND token=$2 AND kind=$3 "
            "AND sent_at > now() - ($4::text || ' minutes')::interval",
            uid, token, kind, str(cooldown_min))
        return row is not None

    async def mark_sent(self, uid: int, token: str, kind: str) -> None:
        await pool().execute(
            "INSERT INTO tg_alert_sent (telegram_id, token, kind) VALUES ($1,$2,$3) "
            "ON CONFLICT (telegram_id, token, kind) DO UPDATE SET sent_at = now()",
            uid, token, kind)

    async def set_discovery_cursor(self, uid: int, block: int) -> None:
        await pool().execute(
            "UPDATE tg_alert_subs SET cursor_block=$2, updated_at=now() WHERE telegram_id=$1",
            uid, block)

    async def set_wallet_cursor(self, sub_id: int, block: int) -> None:
        await pool().execute(
            "UPDATE tg_tracked_wallets SET cursor_block=$2 WHERE id=$1", sub_id, block)

    # ── handler CRUD ───────────────────────────────────────────────────────
    async def add_tracked_wallet(self, uid: int, address: str, seed_block: int,
                                 label: str | None = None) -> bool:
        """Track a wallet, seeded to the current tip (alerts start now, no backfill).
        Returns False if already tracked."""
        res = await pool().execute(
            "INSERT INTO tg_tracked_wallets (telegram_id, address, label, cursor_block) "
            "VALUES ($1,$2,$3,$4) ON CONFLICT (telegram_id, address) DO NOTHING",
            uid, address.lower(), label, seed_block)
        return res.endswith("1")

    async def remove_tracked_wallet(self, uid: int, address: str) -> bool:
        res = await pool().execute(
            "DELETE FROM tg_tracked_wallets WHERE telegram_id=$1 AND address=$2",
            uid, address.lower())
        return res.endswith("1")

    async def list_tracked(self, uid: int) -> list[dict]:
        rows = await pool().fetch(
            "SELECT address, label, cursor_block FROM tg_tracked_wallets "
            "WHERE telegram_id=$1 ORDER BY created_at DESC", uid)
        return [dict(r) for r in rows]

    async def get_sub(self, uid: int) -> dict | None:
        row = await pool().fetchrow(
            "SELECT smart_alerts, min_smart, new_only, cursor_block "
            "FROM tg_alert_subs WHERE telegram_id=$1", uid)
        return dict(row) if row else None

    async def set_smart_alerts(self, uid: int, on: bool, seed_block: int,
                               min_smart: int = 3, new_only: bool = False) -> None:
        """Enable/disable the discovery feed. On enable, seed the cursor to the tip
        so alerts start now (never a historical dump)."""
        await pool().execute(
            "INSERT INTO tg_alert_subs (telegram_id, smart_alerts, min_smart, new_only, cursor_block) "
            "VALUES ($1,$2,$3,$4,$5) "
            "ON CONFLICT (telegram_id) DO UPDATE SET "
            "  smart_alerts=EXCLUDED.smart_alerts, min_smart=EXCLUDED.min_smart, "
            "  new_only=EXCLUDED.new_only, "
            "  cursor_block=CASE WHEN EXCLUDED.smart_alerts AND NOT tg_alert_subs.smart_alerts "
            "                    THEN EXCLUDED.cursor_block ELSE tg_alert_subs.cursor_block END, "
            "  updated_at=now()",
            uid, on, min_smart, new_only, seed_block)
