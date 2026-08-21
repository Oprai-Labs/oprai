"""SQLAlchemy model for user_consents table in memory_schema."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings


class Base(DeclarativeBase):
    """Shared declarative base for all memory-service models."""


class UserConsent(Base):
    """Per-user consent flags for each memory type.

    Controls which kinds of data the memory service is allowed to
    persist in Qdrant for a given user.
    """

    __tablename__ = "user_consents"
    __table_args__ = {"schema": settings.DB_SCHEMA}

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    position: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    contract: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    strategy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    preference: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    decision: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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
