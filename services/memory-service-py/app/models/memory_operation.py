"""SQLAlchemy model for memory_operations audit trail table in memory_schema."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.consent import Base
from app.config import settings


class MemoryOperation(Base):
    """Immutable audit record for a single vector memory operation.

    Written by :func:`app.services.audit_service.log_memory_op` after every
    insert, retrieve, delete, update, search, or clear call.  Rows are never
    updated — new rows are appended only.

    The ``metadata_`` column stores arbitrary JSON context (hashed query text,
    embedding model name, etc.).  It is named with a trailing underscore to
    avoid shadowing the built-in ``metadata`` attribute used by SQLAlchemy's
    ``DeclarativeBase``.
    """

    __tablename__ = "memory_operations"
    __table_args__ = {"schema": settings.DB_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    wallet_address: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # 'insert' | 'retrieve' | 'delete' | 'update' | 'search' | 'clear'
    operation: Mapped[str] = mapped_column(String(50), nullable=False)
    collection: Mapped[str] = mapped_column(String(255), nullable=False)

    vector_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_stored: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 'position' | 'contract' | 'strategy' | 'preference' | 'decision' | 'general'
    memory_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Arbitrary JSON context — hashed query text, model used, top-k, etc.
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
