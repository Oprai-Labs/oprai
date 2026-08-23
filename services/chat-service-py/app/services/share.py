"""Public share links for chat sessions.

A share publishes ONE conversation behind an opaque token. The token is the
only credential: anyone holding it reads the chat without a wallet, and
nobody without it can reach the chat at all — there is no listing, no
enumeration, and no id in the URL that maps to anything guessable.

Two rules run through every function here:

1. **The snapshot is the share.** `shared_up_to` freezes what the link
   publishes. Messages written after that instant are not part of the share
   and are never returned, so continuing a conversation you shared once does
   not keep publishing it. Refreshing the link is the owner's explicit act.
2. **A share is read-only, forever.** Nothing in this module exposes a write
   path, and the public resolver returns messages only — no session id, no
   wallet, nothing a visitor could use against the authenticated API.
"""

import secrets
import uuid
from datetime import datetime, timezone, UTC

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import ChatMessage
from app.models.session import ChatSession
from app.models.share import ChatSessionShare
# Reuse the account-aware ownership predicate so shares scope to the ACCOUNT
# (one account, many wallets), not the single wallet that created the row.
from app.services.session import _owner_where

# 32 URL-safe bytes ≈ 43 characters of base64url. Long enough that guessing
# is not a threat model, short enough to paste into a chat window.
_TOKEN_BYTES = 32

# Mirrors services.message.get_messages — the public view must show exactly
# what the owner sees, minus anything that would only make sense to them.
_PUBLIC_METADATA_KEYS = (
    "action_results",
    "query_snapshots",
    "actions",
    "queries",
    "clarifications",
    "protocols",
    "edited_at",
)


def _serialize(share: ChatSessionShare) -> dict:
    return {
        "token": share.token,
        "sessionId": str(share.session_id),
        "title": share.title,
        "sharedUpTo": share.shared_up_to.isoformat(),
        "viewCount": share.view_count,
        "createdAt": share.created_at.isoformat(),
        "updatedAt": share.updated_at.isoformat(),
    }


async def _owned_session(
    db: AsyncSession, wallet: str, session_id: str, account_id: str | None = None
) -> ChatSession | None:
    """Return the session only if this ACCOUNT owns it and it still exists."""
    try:
        sid = uuid.UUID(session_id)
    except (ValueError, AttributeError):
        return None
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == sid,
            _owner_where(wallet, account_id),
            ChatSession.is_deleted == False,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


async def get_share(
    db: AsyncSession, wallet: str, session_id: str, account_id: str | None = None
) -> dict | None:
    """Return the account's share for a session, or None if it isn't shared."""
    # Ownership is by the SESSION (account-scoped), not the share row's creator
    # wallet — a sibling wallet of the same account sees the account's share.
    if await _owned_session(db, wallet, session_id, account_id) is None:
        return None
    sid = uuid.UUID(session_id)  # valid: _owned_session returned a row
    result = await db.execute(
        select(ChatSessionShare).where(ChatSessionShare.session_id == sid)
    )
    share = result.scalar_one_or_none()
    return _serialize(share) if share else None


async def create_or_refresh(
    db: AsyncSession, wallet: str, session_id: str, account_id: str | None = None
) -> dict | None:
    """Publish a session, or move an existing link's snapshot up to now.

    Returns None when the wallet does not own the session, so the caller
    answers 404 rather than confirming that some other wallet's chat exists.

    An existing link keeps its token. The URL may already be in a message
    someone else is reading; minting a new token on every refresh would break
    it silently, and the user asked to update the link, not replace it.
    """
    session = await _owned_session(db, wallet, session_id, account_id)
    if session is None:
        return None

    now = datetime.now(UTC)
    result = await db.execute(
        select(ChatSessionShare).where(ChatSessionShare.session_id == session.id)
    )
    share = result.scalar_one_or_none()

    if share is None:
        share = ChatSessionShare(
            id=uuid.uuid4(),
            session_id=session.id,
            wallet_address=wallet,
            token=secrets.token_urlsafe(_TOKEN_BYTES),
            shared_up_to=now,
            title=session.title or "Shared chat",
        )
        db.add(share)
    else:
        share.shared_up_to = now
        share.title = session.title or share.title

    await db.flush()
    await db.refresh(share)
    return _serialize(share)


async def revoke(
    db: AsyncSession, wallet: str, session_id: str, account_id: str | None = None
) -> bool:
    """Delete the share. The token stops resolving immediately and for good."""
    # Account-scoped ownership of the session, not the share's creator wallet.
    if await _owned_session(db, wallet, session_id, account_id) is None:
        return False
    sid = uuid.UUID(session_id)  # valid: _owned_session returned a row
    result = await db.execute(
        delete(ChatSessionShare).where(ChatSessionShare.session_id == sid)
    )
    return (result.rowcount or 0) > 0


async def list_shares(
    db: AsyncSession, wallet: str, account_id: str | None = None
) -> list[dict]:
    """Every link this ACCOUNT has published, newest first."""
    # Shares of any session the account owns — including those created by a
    # sibling wallet — not just rows stamped with the current wallet.
    owned_sessions = select(ChatSession.id).where(_owner_where(wallet, account_id))
    result = await db.execute(
        select(ChatSessionShare)
        .where(ChatSessionShare.session_id.in_(owned_sessions))
        .order_by(ChatSessionShare.created_at.desc())
    )
    return [_serialize(s) for s in result.scalars().all()]


async def resolve_public(db: AsyncSession, token: str) -> dict | None:
    """Resolve a share token to the published conversation, or None.

    This is the only function reachable without a wallet. What it returns is
    deliberately narrow: the frozen title, the share timestamp, and the
    messages up to the cutoff. No session id, no owner wallet, nothing a
    visitor could carry over to the authenticated API.
    """
    if not token or len(token) > 64:
        return None

    result = await db.execute(
        select(ChatSessionShare).where(ChatSessionShare.token == token)
    )
    share = result.scalar_one_or_none()
    if share is None:
        return None

    rows = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.session_id == share.session_id,
            # The cutoff. Everything the owner wrote after sharing is not
            # part of this link and must never appear behind it.
            ChatMessage.created_at <= share.shared_up_to,
        )
        .order_by(ChatMessage.created_at.asc())
    )
    messages = [
        m for m in rows.scalars().all()
        if not (m.metadata_ or {}).get("superseded_at")
    ]

    # Best-effort counter: a failed increment must never cost a visitor the
    # page they came for, so it rides the same transaction and nothing reads
    # it back.
    await db.execute(
        update(ChatSessionShare)
        .where(ChatSessionShare.id == share.id)
        .values(view_count=ChatSessionShare.view_count + 1)
    )

    return {
        "share": {
            "title": share.title,
            "sharedAt": share.shared_up_to.isoformat(),
        },
        "messages": [
            {
                "id": str(m.id),
                "speaker": m.role,
                "content": m.content,
                "timestamp": m.created_at.isoformat(),
                "metadata": {
                    k: v for k, v in (m.metadata_ or {}).items()
                    if k in _PUBLIC_METADATA_KEYS
                },
            }
            for m in messages
        ],
    }
