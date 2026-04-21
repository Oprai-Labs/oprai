"""SQLAlchemy model for chat_messages table."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.models.session import Base


class ChatMessage(Base):
    """Represents a single message within a chat session."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_session_created", "session_id", "created_at"),
        Index("ix_chat_messages_wallet_address", "wallet_address"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{settings.DB_SCHEMA}.chat_sessions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    wallet_address: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="user | assistant | system",
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=True,
    )
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
