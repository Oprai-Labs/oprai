"""Session CRUD operations against chat_sessions table."""

import uuid
from datetime import datetime, timezone, UTC

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import ChatSession


def _coerce_account(account_id: str | None) -> uuid.UUID | None:
    """A valid account UUID, or None (header absent / malformed / legacy token)."""
    if not account_id:
        return None
    try:
        return uuid.UUID(str(account_id))
    except (ValueError, AttributeError, TypeError):
        return None


def _coerce_session_id(session_id: str) -> uuid.UUID | None:
    """A valid session UUID, or None. A malformed path param becomes a clean
    404 / no-op at the caller instead of an uncaught ValueError → 500."""
    try:
        return uuid.UUID(str(session_id))
    except (ValueError, AttributeError, TypeError):
        return None


def _owner_where(wallet: str, account_id: str | None):
    """The ownership predicate for a session row.

    With a multichain account, a session belongs to the ACCOUNT — so every wallet
    linked to it sees the same history. Legacy sessions predate accounts and have
    a NULL account_id; those stay scoped to the wallet that created them. So:
      account known  → account matches, OR (legacy row with no account AND this
                        wallet created it)
      no account     → wallet scoping only (old token / unlinked)
    This is the single source of truth every query below uses, so read and
    mutation access can never diverge (no "you can see it but not delete it").
    """
    acct = _coerce_account(account_id)
    if acct is not None:
        return or_(
            ChatSession.account_id == acct,
            and_(ChatSession.account_id.is_(None), ChatSession.wallet_address == wallet),
        )
    return ChatSession.wallet_address == wallet


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
    account_id: str | None = None,
) -> tuple[list[dict], bool, str | None]:
    """Return paginated non-deleted sessions for the account (or wallet), newest first.

    Returns (sessions, has_more, next_cursor).
    """
    stmt = (
        select(ChatSession)
        .where(
            _owner_where(wallet, account_id),
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
    account_id: str | None = None,
) -> dict:
    """Create a new chat session and return its serialized form.

    Stamps the multichain account so the session is visible from every linked
    wallet. Falls back to wallet-only scoping when no account is known.
    """
    session = ChatSession(
        id=uuid.uuid4(),
        user_id=user_id,
        wallet_address=wallet,
        account_id=_coerce_account(account_id),
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
    account_id: str | None = None,
) -> dict | None:
    """Get a single non-deleted session by id, scoped to the account (or wallet)."""
    sid = _coerce_session_id(session_id)
    if sid is None:
        return None
    stmt = select(ChatSession).where(
        ChatSession.id == sid,
        _owner_where(wallet, account_id),
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
    account_id: str | None = None,
) -> bool:
    """Update a session title. Returns True if a row was affected."""
    sid = _coerce_session_id(session_id)
    if sid is None:
        return False
    stmt = (
        update(ChatSession)
        .where(
            ChatSession.id == sid,
            _owner_where(wallet, account_id),
            ChatSession.is_deleted == False,  # noqa: E712
        )
        .values(title=title, updated_at=datetime.now(UTC))
    )
    result = await db.execute(stmt)
    return (result.rowcount or 0) > 0


async def set_pinned(
    db: AsyncSession,
    wallet: str,
    session_id: str,
    pinned: bool,
    account_id: str | None = None,
) -> bool:
    """Set the pinned state of a session. Returns True if a row was affected."""
    sid = _coerce_session_id(session_id)
    if sid is None:
        return False
    now = datetime.now(UTC)
    stmt = (
        update(ChatSession)
        .where(
            ChatSession.id == sid,
            _owner_where(wallet, account_id),
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
    account_id: str | None = None,
) -> bool:
    """Soft-delete a session. Returns True if a row was affected."""
    sid = _coerce_session_id(session_id)
    if sid is None:
        return False
    now = datetime.now(UTC)
    stmt = (
        update(ChatSession)
        .where(
            ChatSession.id == sid,
            _owner_where(wallet, account_id),
            ChatSession.is_deleted == False,  # noqa: E712
        )
        .values(is_deleted=True, deleted_at=now)
    )
    result = await db.execute(stmt)
    return (result.rowcount or 0) > 0


async def delete_all_sessions(db: AsyncSession, wallet: str, account_id: str | None = None) -> int:
    """Soft-delete every session belonging to the account (or wallet). Returns the row count.

    Used by the Settings → Privacy "delete chat history" action. Soft-delete
    keeps the rows for audit / recovery; a follow-up cron can purge.
    """
    now = datetime.now(UTC)
    stmt = (
        update(ChatSession)
        .where(
            _owner_where(wallet, account_id),
            ChatSession.is_deleted == False,  # noqa: E712
        )
        .values(is_deleted=True, deleted_at=now)
    )
    result = await db.execute(stmt)
    return result.rowcount or 0
