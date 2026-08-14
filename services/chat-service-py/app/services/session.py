"""Session CRUD operations against chat_sessions table."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import ChatSession


def _serialize(s: ChatSession) -> dict:
    return {
        "id": str(s.id),
        "wallet": s.wallet_address,
        "title": s.title,
        "pinned": s.pinned,
        "createdAt": s.created_at.isoformat(),
        "updatedAt": s.updated_at.isoformat(),
        # Activity time — when a message last landed here. `updatedAt` moves
        # on any row change (rename, pin), so it cannot answer "how recent is
        # this conversation"; the sidebar groups on this instead.
        "lastMessageAt": s.last_message_at.isoformat() if s.last_message_at else None,
        # Per-chat usage — surfaced so the frontend can render the locked
        # composer state and a "X / Y tokens used" badge in the sidebar.
        "messageCount": s.message_count,
        "totalTokens":  int(s.total_tokens or 0),
        "isLocked":     bool(s.is_locked),
        "lockedReason": s.locked_reason,
    }


async def list_sessions(
    db: AsyncSession,
    wallet: str,
    limit: int = 20,
    cursor: str | None = None,
) -> tuple[list[dict], bool, str | None]:
    """Return paginated non-deleted sessions for a wallet, newest first.

    Returns (sessions, has_more, next_cursor).
    """
    stmt = (
        select(ChatSession)
        .where(
            ChatSession.wallet_address == wallet,
            ChatSession.is_deleted == False,  # noqa: E712
        )
        .order_by(ChatSession.updated_at.desc())
    )

    if cursor:
        cursor_dt = datetime.fromisoformat(cursor)
        stmt = stmt.where(ChatSession.updated_at < cursor_dt)

    stmt = stmt.limit(limit + 1)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    has_more = len(rows) > limit
    sessions = rows[:limit]

    next_cursor: str | None = None
    if has_more and sessions:
        next_cursor = sessions[-1].updated_at.isoformat()

    return [_serialize(s) for s in sessions], has_more, next_cursor


async def create_session(
    db: AsyncSession,
    wallet: str,
    user_id: str,
    title: str | None = None,
) -> dict:
    """Create a new chat session and return its serialized form."""
    session = ChatSession(
        id=uuid.uuid4(),
        user_id=user_id,
        wallet_address=wallet,
        title=title or "New chat",
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return _serialize(session)


async def get_session(
    db: AsyncSession,
    wallet: str,
    session_id: str,
) -> dict | None:
    """Get a single non-deleted session by id, scoped to wallet."""
    stmt = select(ChatSession).where(
        ChatSession.id == uuid.UUID(session_id),
        ChatSession.wallet_address == wallet,
        ChatSession.is_deleted == False,  # noqa: E712
    )
    result = await db.execute(stmt)
    s = result.scalar_one_or_none()
    if s is None:
        return None
    return {
        **_serialize(s),
        "messageCount": s.message_count,
    }


async def update_title(
    db: AsyncSession,
    wallet: str,
    session_id: str,
    title: str,
) -> bool:
    """Update a session title. Returns True if a row was affected."""
    stmt = (
        update(ChatSession)
        .where(
            ChatSession.id == uuid.UUID(session_id),
            ChatSession.wallet_address == wallet,
            ChatSession.is_deleted == False,  # noqa: E712
        )
        .values(title=title, updated_at=datetime.now(timezone.utc))
    )
    result = await db.execute(stmt)
    return (result.rowcount or 0) > 0


async def set_pinned(
    db: AsyncSession,
    wallet: str,
    session_id: str,
    pinned: bool,
) -> bool:
    """Set the pinned state of a session. Returns True if a row was affected."""
    now = datetime.now(timezone.utc)
    stmt = (
        update(ChatSession)
        .where(
            ChatSession.id == uuid.UUID(session_id),
            ChatSession.wallet_address == wallet,
            ChatSession.is_deleted == False,  # noqa: E712
        )
        .values(pinned=pinned, pinned_at=now if pinned else None)
    )
    result = await db.execute(stmt)
    return (result.rowcount or 0) > 0


async def delete_session(
    db: AsyncSession,
    wallet: str,
    session_id: str,
) -> bool:
    """Soft-delete a session. Returns True if a row was affected."""
    now = datetime.now(timezone.utc)
    stmt = (
        update(ChatSession)
        .where(
            ChatSession.id == uuid.UUID(session_id),
            ChatSession.wallet_address == wallet,
            ChatSession.is_deleted == False,  # noqa: E712
        )
        .values(is_deleted=True, deleted_at=now)
    )
    result = await db.execute(stmt)
    return (result.rowcount or 0) > 0


async def delete_all_sessions(db: AsyncSession, wallet: str) -> int:
    """Soft-delete every session belonging to a wallet. Returns the row count.

    Used by the Settings → Privacy "delete chat history" action. Soft-delete
    keeps the rows for audit / recovery; a follow-up cron can purge.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        update(ChatSession)
        .where(
            ChatSession.wallet_address == wallet,
            ChatSession.is_deleted == False,  # noqa: E712
        )
        .values(is_deleted=True, deleted_at=now)
    )
    result = await db.execute(stmt)
    return result.rowcount or 0
