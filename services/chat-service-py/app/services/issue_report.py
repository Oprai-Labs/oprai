"""User-submitted issue reports (Help → Report Issue).

Deliberately thin: a report is a row, and the value is that it reaches the
admin queue at all. The one piece of judgement here is input bounding —
these fields are free text typed by anyone with a wallet, and they are
rendered back in the admin panel, so length limits are enforced at write
time rather than trusted from the client.
"""

import json
import uuid
from datetime import datetime, timezone, UTC

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

_CATEGORIES = ("bug", "feature", "account", "other")

# Bounds, not validation theatre: the DB columns are TEXT, so without these a
# single report could carry a megabyte of prose into every admin page load.
_MAX_SUBJECT = 160
_MAX_DESCRIPTION = 4000
# The context bag is ours, not the user's — but the route and user agent
# inside it still originate in the browser.
_MAX_CONTEXT_VALUE = 500


def _clean(value: str | None, limit: int) -> str:
    return (value or "").strip()[:limit]


async def create_report(
    db: AsyncSession,
    wallet: str,
    category: str,
    subject: str,
    description: str,
    context: dict | None = None,
) -> dict:
    """Record a report. Returns the stored row."""
    cat = category if category in _CATEGORIES else "other"
    ctx = {
        str(k)[:64]: _clean(str(v), _MAX_CONTEXT_VALUE)
        for k, v in (context or {}).items()
    }

    report_id = uuid.uuid4()
    now = datetime.now(UTC)
    await db.execute(
        text(
            f"""
            INSERT INTO {settings.DB_SCHEMA}.issue_reports
                (id, wallet, category, subject, description, context, status, created_at, updated_at)
            VALUES
                (:id, :wallet, :category, :subject, :description, CAST(:context AS jsonb), 'open', :now, :now)
            """
        ),
        {
            "id": report_id,
            "wallet": wallet,
            "category": cat,
            "subject": _clean(subject, _MAX_SUBJECT) or "(no subject)",
            "description": _clean(description, _MAX_DESCRIPTION),
            "context": json.dumps(ctx),
            "now": now,
        },
    )
    return {
        "id": str(report_id),
        "category": cat,
        "subject": _clean(subject, _MAX_SUBJECT) or "(no subject)",
        "description": _clean(description, _MAX_DESCRIPTION),
        "status": "open",
        "createdAt": now.isoformat(),
    }


async def list_own_reports(db: AsyncSession, wallet: str, limit: int = 20) -> list[dict]:
    """This wallet's own reports, newest first.

    Scoped to the caller — a user sees what they sent and its current status,
    and nothing anyone else sent.
    """
    rows = await db.execute(
        text(
            f"""
            SELECT id, category, subject, description, status, admin_note, created_at, updated_at
              FROM {settings.DB_SCHEMA}.issue_reports
             WHERE wallet = :wallet
             ORDER BY created_at DESC
             LIMIT :limit
            """
        ),
        {"wallet": wallet, "limit": max(1, min(limit, 50))},
    )
    return [
        {
            "id": str(r.id),
            "category": r.category,
            "subject": r.subject,
            "description": r.description,
            "status": r.status,
            "adminNote": r.admin_note,
            "createdAt": r.created_at.isoformat(),
            "updatedAt": r.updated_at.isoformat(),
        }
        for r in rows
    ]


async def count_recent(db: AsyncSession, wallet: str, within_minutes: int = 10) -> int:
    """How many reports this wallet filed recently — backs the submit throttle.

    Without it, the form is an unauthenticated-feeling write endpoint that any
    signed-in wallet can hold down to fill the admin queue with noise.
    """
    row = await db.execute(
        text(
            f"""
            SELECT COUNT(*) AS n
              FROM {settings.DB_SCHEMA}.issue_reports
             WHERE wallet = :wallet
               AND created_at > NOW() - (:mins * INTERVAL '1 minute')
            """
        ),
        {"wallet": wallet, "mins": within_minutes},
    )
    return int(row.scalar() or 0)
