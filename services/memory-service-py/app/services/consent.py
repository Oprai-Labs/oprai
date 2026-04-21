"""Consent CRUD operations against user_consents table.

Manages per-user consent flags that control which types of memory
the system is allowed to persist in Qdrant.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.consent import UserConsent

# Types that require consent (excludes "meta")
CONSENT_FIELDS = ("position", "contract", "strategy", "preference", "decision")


async def get_consent(
    db: AsyncSession,
    user_id: str,
) -> dict[str, bool]:
    """Fetch consent flags for a user.

    Returns an empty dict if no consent record exists.
    """
    stmt = select(UserConsent).where(UserConsent.user_id == user_id)
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        return {}

    return {
        "position": row.position,
        "contract": row.contract,
        "strategy": row.strategy,
        "preference": row.preference,
        "decision": row.decision,
    }


async def update_consent(
    db: AsyncSession,
    user_id: str,
    fields: dict[str, Any],
) -> dict[str, bool]:
    """Create or update consent flags for a user.

    Only recognised fields (position, contract, strategy, preference,
    decision) are applied; everything else is ignored.

    Returns the full consent map after the upsert.
    """
    stmt = select(UserConsent).where(UserConsent.user_id == user_id)
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        row = UserConsent(
            user_id=user_id,
            position=fields.get("position", False),
            contract=fields.get("contract", False),
            strategy=fields.get("strategy", False),
            preference=fields.get("preference", False),
            decision=fields.get("decision", False),
        )
        db.add(row)
    else:
        for field in CONSENT_FIELDS:
            if field in fields:
                setattr(row, field, bool(fields[field]))

    await db.flush()
    await db.refresh(row)

    return {
        "position": row.position,
        "contract": row.contract,
        "strategy": row.strategy,
        "preference": row.preference,
        "decision": row.decision,
    }


def is_type_allowed(consent: dict[str, bool], memory_type: str) -> bool:
    """Check whether a memory type is permitted by the user's consent.

    The ``meta`` type is always allowed regardless of consent.
    """
    if memory_type == "meta":
        return True
    return bool(consent.get(memory_type, False))
