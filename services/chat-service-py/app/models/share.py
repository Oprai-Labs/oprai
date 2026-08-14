"""SQLAlchemy model for chat_session_shares (public read-only chat links)."""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.models.session import Base


class ChatSessionShare(Base):
    """A public link to one chat, readable without a wallet.

    One row per shared session (``UNIQUE(session_id)``): re-sharing a chat
    refreshes the existing row rather than minting a second link, so a
    conversation never has two live URLs the owner has to track separately.
    Revoking deletes the row — the token dies with it and cannot be revived,
    which is the only revocation a user would call revocation.
    """

    __tablename__ = "chat_session_shares"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_session_shares_session"),
        Index("ix_session_shares_wallet", "wallet_address", "created_at"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.DB_SCHEMA}.chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Denormalised from the session so "list my shared links" and "is this
    # mine" never need a join, and so a share survives being read by the
    # public endpoint without touching the sessions table at all.
    wallet_address: Mapped[str] = mapped_column(String(255), nullable=False)

    # The URL secret. Unguessable and the ONLY credential the public endpoint
    # accepts — indexed unique because every public read looks up by it.
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    # The snapshot cutoff. The public endpoint returns messages created at or
    # before this instant and nothing after it, so anything the owner writes
    # in the conversation AFTER sharing stays private until they explicitly
    # refresh the link. A live link would silently publish every future turn.
    shared_up_to: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Title as it read when the link was last refreshed. Renaming a chat
    # afterwards is a private act and must not rewrite what visitors see.
    title: Mapped[str] = mapped_column(String, nullable=False, default="Shared chat")

    view_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
