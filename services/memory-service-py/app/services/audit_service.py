"""Audit trail writer for vector memory operations.

All callers should use :func:`log_memory_op`.  The function is intentionally
fire-resistant: any exception raised during the write (DB unavailable,
constraint violation, etc.) is caught, logged at ERROR level, and then
silently discarded.  Audit failures must never surface to the user or
interrupt the operation being audited.
"""

import logging
import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory_operation import MemoryOperation

logger = logging.getLogger(__name__)


async def log_memory_op(
    db: AsyncSession,
    operation: str,
    collection: str,
    *,
    user_id: str | None = None,
    wallet_address: str | None = None,
    vector_count: int = 0,
    bytes_stored: int = 0,
    memory_type: str | None = None,
    metadata: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    success: bool = True,
    error_message: str | None = None,
) -> None:
    """Append an audit record to ``memory_schema.memory_operations``.

    Parameters
    ----------
    db:
        An open :class:`sqlalchemy.ext.asyncio.AsyncSession`.  The caller
        owns the transaction — this function calls ``flush()`` only, never
        ``commit()``.
    operation:
        One of ``'insert'``, ``'retrieve'``, ``'delete'``, ``'update'``,
        ``'search'``, or ``'clear'``.
    collection:
        Qdrant collection name that was targeted.
    user_id:
        Authenticated user UUID (string form).  ``None`` for anonymous ops.
    wallet_address:
        Solana wallet address string.  Stored as-is (not validated here).
    vector_count:
        Number of vectors affected by the operation.
    bytes_stored:
        Approximate payload size in bytes (used for capacity planning).
    memory_type:
        Memory category: ``'position'``, ``'contract'``, ``'strategy'``,
        ``'preference'``, ``'decision'``, or ``'general'``.
    metadata:
        Arbitrary JSON context — hashed query text, model name, top-k, etc.
    duration_ms:
        Wall-clock duration of the audited operation in milliseconds.
    success:
        ``True`` if the operation completed without error.
    error_message:
        Human-readable error string when *success* is ``False``.

    Notes
    -----
    This function never raises.  All exceptions are caught and emitted
    as ``logger.error`` entries so the caller's hot path is unaffected.
    """
    try:
        # Coerce string UUIDs to uuid.UUID objects; leave None as-is.
        parsed_user_id: uuid.UUID | None = None
        if user_id is not None:
            try:
                parsed_user_id = uuid.UUID(str(user_id))
            except (ValueError, AttributeError):
                # Non-UUID user_id (e.g. a plain string key) — store as None
                # rather than failing the audit write entirely.
                logger.debug(
                    "log_memory_op: user_id '%s' is not a valid UUID, storing NULL",
                    user_id,
                )

        op_row = MemoryOperation(
            user_id=parsed_user_id,
            wallet_address=str(wallet_address) if wallet_address is not None else None,
            operation=operation,
            collection=collection,
            vector_count=vector_count,
            bytes_stored=bytes_stored,
            memory_type=memory_type,
            metadata_=metadata,
            duration_ms=duration_ms,
            success=success,
            error_message=error_message,
        )
        db.add(op_row)
        # Flush assigns the server-generated ``id`` and validates constraints
        # without committing — the caller's session controls the transaction.
        await db.flush([op_row])

    except Exception as exc:
        logger.error(
            "Failed to write memory audit log [op=%s collection=%s success=%s]: %s",
            operation,
            collection,
            success,
            exc,
            exc_info=True,
        )
