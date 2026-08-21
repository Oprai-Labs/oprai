"""SQLAlchemy model for chat_summaries table."""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.models.session import Base


class ChatSummary(Base):
    """Stores block-level summaries for a chat session.

    Each block covers 10 messages.  Block 0 summarises messages 1-10,
    block 1 summarises messages 11-20, etc.
    """

    __tablename__ = "chat_summaries"
    __table_args__ = (
        UniqueConstraint("session_id", "block_index", name="uq_summary_session_block"),
        Index("ix_chat_summaries_session_block", "session_id", "block_index"),
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
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    message_start: Mapped[int] = mapped_column(Integer, nullable=False)
    message_end: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
