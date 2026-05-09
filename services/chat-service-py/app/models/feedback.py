"""SQLAlchemy model for message_feedback (👍 / 👎)."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.models.session import Base


class MessageFeedback(Base):
    """Per-message rating (+1 = thumbs up, -1 = thumbs down)."""

    __tablename__ = "message_feedback"
    __table_args__ = (
        UniqueConstraint("message_id", "wallet", name="uq_feedback_message_wallet"),
        CheckConstraint("rating IN (-1, 1)", name="ck_feedback_rating"),
        Index("idx_message_feedback_rating", "rating", "created_at"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.DB_SCHEMA}.chat_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    wallet: Mapped[str] = mapped_column(String(255), nullable=False)
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
