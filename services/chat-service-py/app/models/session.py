"""SQLAlchemy model for chat_sessions table."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings


class Base(DeclarativeBase):
    """Shared declarative base for all chat-service models."""


class ChatSession(Base):
    """Represents a chat session owned by a wallet address."""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_sessions_wallet_created", "wallet_address", "created_at"),
        Index("ix_chat_sessions_user_id", "user_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    wallet_address: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False, default="New chat")
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    pinned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When this conversation last had a MESSAGE — maintained by a trigger on
    # chat_messages insert (migration 20260420), not by the ORM.
    #
    # Distinct from `updated_at`, which is row-modification time and therefore
    # moves when the title is edited or the chat is pinned. Those are not
    # activity: renaming a chat from last month must not file it under Today.
    # Anything answering "how recent is this conversation" reads this column.
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # High-level state extracted after each assistant turn. Survives block
    # summarisation and is injected as a single system message so the model
    # always knows the user's current intent + active entities even when the
    # raw history has been compressed away. See `services.session_state`.
    session_state: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict,
    )
    # Per-chat usage counters. `total_tokens` is the running sum of input +
    # output tokens charged to this conversation. `is_locked` is the durable
    # flag set when the chat hits its per-chat cap — the frontend disables
    # the composer for any locked session and forces a new chat. Both are
    # set by the cost_cap pipeline after each assistant turn.
    total_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0", default=0,
    )
    is_locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False,
    )
    locked_reason: Mapped[str | None] = mapped_column(
        String, nullable=True,
    )
