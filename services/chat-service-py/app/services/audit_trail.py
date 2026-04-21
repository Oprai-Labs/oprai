"""
Audit Trail Service

Comprehensive logging of all user actions and system events:
- User authentication events (login, logout, failed attempts)
- Transaction events (created, signed, submitted, confirmed, failed)
- API requests and responses
- Preference changes
- System events (errors, warnings, info)
- Security events (suspicious activity)

Uses PostgreSQL for persistence with JSONB for flexible event data.
"""

import json
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_, or_

import redis.asyncio as redis
from app.services.cache import get_redis_client

logger = logging.getLogger(__name__)


# ============================================================================
# Enums
# ============================================================================

class AuditEventType(str, Enum):
    """Types of audit events"""

    # Authentication events
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    SIGNUP = "signup"

    # Transaction events
    TRANSACTION_CREATED = "transaction_created"
    TRANSACTION_SIGNED = "transaction_signed"
    TRANSACTION_SUBMITTED = "transaction_submitted"
    TRANSACTION_CONFIRMED = "transaction_confirmed"
    TRANSACTION_FAILED = "transaction_failed"
    TRANSACTION_CANCELLED = "transaction_cancelled"

    # Action events
    ACTION_EXECUTED = "action_executed"
    ACTION_SIMULATED = "action_simulated"
    ACTION_APPROVED = "action_approved"
    ACTION_REJECTED = "action_rejected"

    # Preference events
    PREFERENCES_UPDATED = "preferences_updated"
    PREFERENCES_RESET = "preferences_reset"

    # API events
    API_REQUEST = "api_request"
    API_RESPONSE = "api_response"
    API_ERROR = "api_error"

    # Security events
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    INVALID_SIGNATURE = "invalid_signature"
    UNAUTHORIZED_ACCESS = "unauthorized_access"

    # System events
    SYSTEM_ERROR = "system_error"
    SYSTEM_WARNING = "system_warning"
    SYSTEM_INFO = "system_info"

    # Wallet events
    WALLET_CONNECTED = "wallet_connected"
    WALLET_DISCONNECTED = "wallet_disconnected"
    WALLET_SWITCHED = "wallet_switched"


class AuditEventSeverity(str, Enum):
    """Severity levels for audit events"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditEntityType(str, Enum):
    """Entity types that can be audited"""
    USER = "user"
    WALLET = "wallet"
    TRANSACTION = "transaction"
    SESSION = "session"
    PREFERENCES = "preferences"
    API_KEY = "api_key"
    SYSTEM = "system"


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class AuditEvent:
    """An audit trail event"""
    id: Optional[str] = None

    # Event identification
    event_type: AuditEventType = AuditEventType.SYSTEM_INFO
    severity: AuditEventSeverity = AuditEventSeverity.INFO

    # Entity information
    entity_type: Optional[AuditEntityType] = None
    entity_id: Optional[str] = None

    # User/Wallet information
    user_id: Optional[str] = None
    wallet_address: Optional[str] = None
    session_id: Optional[str] = None

    # Event data (flexible JSON)
    event_data: Dict[str, Any] = field(default_factory=dict)

    # Request context
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_method: Optional[str] = None
    request_path: Optional[str] = None
    request_id: Optional[str] = None

    # Response info
    response_status: Optional[int] = None
    response_time_ms: Optional[float] = None

    # Transaction specific
    transaction_signature: Optional[str] = None
    transaction_amount: Optional[float] = None
    transaction_token: Optional[str] = None

    # Timestamps
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "event_type": self.event_type.value if isinstance(self.event_type, Enum) else self.event_type,
            "severity": self.severity.value if isinstance(self.severity, Enum) else self.severity,
            "entity_type": self.entity_type.value if isinstance(self.entity_type, Enum) else self.entity_type,
            "entity_id": self.entity_id,
            "user_id": self.user_id,
            "wallet_address": self.wallet_address,
            "session_id": self.session_id,
            "event_data": self.event_data,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "request_method": self.request_method,
            "request_path": self.request_path,
            "request_id": self.request_id,
            "response_status": self.response_status,
            "response_time_ms": self.response_time_ms,
            "transaction_signature": self.transaction_signature,
            "transaction_amount": self.transaction_amount,
            "transaction_token": self.transaction_token,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class AuditQuery:
    """Query parameters for audit events"""
    user_id: Optional[str] = None
    wallet_address: Optional[str] = None
    event_type: Optional[AuditEventType] = None
    severity: Optional[AuditEventSeverity] = None
    entity_type: Optional[AuditEntityType] = None
    entity_id: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    request_path: Optional[str] = None
    limit: int = 100
    offset: int = 0


# ============================================================================
# Database Table Model
# ============================================================================

"""
CREATE TABLE IF NOT EXISTS auth_schema.audit_trail (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Event identification
    event_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL DEFAULT 'info',

    -- Entity information
    entity_type VARCHAR(50),
    entity_id VARCHAR(255),

    -- User/Wallet information
    user_id UUID,
    wallet_address VARCHAR(255),
    session_id VARCHAR(255),

    -- Event data (flexible JSON)
    event_data JSONB DEFAULT '{}',

    -- Request context
    ip_address VARCHAR(45),
    user_agent TEXT,
    request_method VARCHAR(10),
    request_path VARCHAR(500),
    request_id VARCHAR(255),

    -- Response info
    response_status INTEGER,
    response_time_ms DECIMAL(10, 2),

    -- Transaction specific
    transaction_signature VARCHAR(255),
    transaction_amount DECIMAL(20, 8),
    transaction_token VARCHAR(50),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_audit_user_id ON auth_schema.audit_trail (user_id);
CREATE INDEX IF NOT EXISTS idx_audit_wallet ON auth_schema.audit_trail (wallet_address);
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON auth_schema.audit_trail (event_type);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON auth_schema.audit_trail (created_at);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON auth_schema.audit_trail (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_severity ON auth_schema.audit_trail (severity);
"""


# ============================================================================
# Service Class
# ============================================================================

class AuditTrailService:
    """
    Service for managing audit trail events.

    Features:
    - Asynchronous event logging
    - Redis caching for recent events
    - Query with filtering and pagination
    - Export functionality
    - Automatic cleanup of old events
    """

    CACHE_TTL_SECONDS = 3600  # 1 hour
    CACHE_KEY_PREFIX = "audit:events:"
    RECENT_EVENTS_CACHE_KEY = "audit:recent:"
    MAX_CACHED_RECENT = 100

    def __init__(self, db_session: Optional[AsyncSession] = None):
        self.db_session = db_session
        self._redis: Optional[redis.Redis] = None

    async def _get_redis(self) -> redis.Redis:
        """Get Redis client"""
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    async def log_event(self, event: AuditEvent) -> bool:
        """
        Log an audit event to the database.

        Args:
            event: The audit event to log

        Returns:
            True if successful
        """
        try:
            # Add to database
            if self.db_session:
                import json

                from sqlalchemy import text

                stmt = text("""
                    INSERT INTO chat_schema.audit_events (
                        event_type, severity, entity_type, entity_id,
                        user_id, wallet_address, session_id, event_data,
                        ip_address, user_agent, request_method, request_path, request_id,
                        response_status, response_time_ms,
                        transaction_signature, transaction_amount, transaction_token,
                        created_at
                    ) VALUES (
                        :event_type, :severity, :entity_type, :entity_id,
                        :user_id, :wallet_address, :session_id, cast(:event_data as jsonb),
                        :ip_address, :user_agent, :request_method, :request_path, :request_id,
                        :response_status, :response_time_ms,
                        :transaction_signature, :transaction_amount, :transaction_token,
                        :created_at
                    )
                """)

                await self.db_session.execute(
                    stmt,
                    {
                        "event_type": event.event_type.value if isinstance(event.event_type, Enum) else event.event_type,
                        "severity": event.severity.value if isinstance(event.severity, Enum) else event.severity,
                        "entity_type": event.entity_type.value if event.entity_type and isinstance(event.entity_type, Enum) else event.entity_type,
                        "entity_id": event.entity_id,
                        "user_id": event.user_id,
                        "wallet_address": event.wallet_address,
                        "session_id": event.session_id,
                        "event_data": json.dumps(event.event_data),
                        "ip_address": event.ip_address,
                        "user_agent": event.user_agent,
                        "request_method": event.request_method,
                        "request_path": event.request_path,
                        "request_id": event.request_id,
                        "response_status": event.response_status,
                        "response_time_ms": event.response_time_ms,
                        "transaction_signature": event.transaction_signature,
                        "transaction_amount": event.transaction_amount,
                        "transaction_token": event.transaction_token,
                        "created_at": event.created_at,
                    }
                )
                await self.db_session.commit()

            # Update recent events cache
            await self._update_recent_cache(event)

            return True

        except Exception as e:
            logger.error("Failed to log audit event", exc_info=True)
            if self.db_session:
                await self.db_session.rollback()
            return False

    async def _update_recent_cache(self, event: AuditEvent) -> None:
        """Update recent events cache"""
        try:
            redis_client = await self._get_redis()
            import json

            # Add to recent events list
            cache_key = f"{self.RECENT_EVENTS_CACHE_KEY}{event.wallet_address or 'anonymous'}"
            event_json = json.dumps(event.to_dict())

            # Add to list and trim
            await redis_client.lpush(cache_key, event_json)
            await redis_client.ltrim(cache_key, 0, self.MAX_CACHED_RECENT - 1)
            await redis_client.expire(cache_key, self.CACHE_TTL_SECONDS)

        except Exception as e:
            logger.warning("Failed to update audit cache", exc_info=True)

    async def query_events(
        self,
        query: AuditQuery,
    ) -> List[AuditEvent]:
        """
        Query audit events with filters.

        Args:
            query: Query parameters

        Returns:
            List of matching audit events
        """
        if not self.db_session:
            return []

        try:
            conditions = []
            params: Dict[str, Any] = {}

            if query.user_id:
                conditions.append("user_id = :user_id")
                params["user_id"] = query.user_id

            if query.wallet_address:
                conditions.append("wallet_address = :wallet_address")
                params["wallet_address"] = query.wallet_address

            if query.event_type:
                conditions.append("event_type = :event_type")
                params["event_type"] = query.event_type.value if isinstance(query.event_type, Enum) else query.event_type

            if query.severity:
                conditions.append("severity = :severity")
                params["severity"] = query.severity.value if isinstance(query.severity, Enum) else query.severity

            if query.entity_type and query.entity_id:
                conditions.append("entity_type = :entity_type AND entity_id = :entity_id")
                params["entity_type"] = query.entity_type.value if isinstance(query.entity_type, Enum) else query.entity_type
                params["entity_id"] = query.entity_id

            if query.start_date:
                conditions.append("created_at >= :start_date")
                params["start_date"] = query.start_date

            if query.end_date:
                conditions.append("created_at <= :end_date")
                params["end_date"] = query.end_date

            if query.request_path:
                conditions.append("request_path LIKE :request_path")
                params["request_path"] = f"%{query.request_path}%"

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            from sqlalchemy import text as _text
            stmt = _text(f"""
                SELECT id, event_type, severity, entity_type, entity_id,
                       user_id, wallet_address, session_id, event_data,
                       ip_address, user_agent, request_method, request_path, request_id,
                       response_status, response_time_ms,
                       transaction_signature, transaction_amount, transaction_token,
                       created_at
                FROM chat_schema.audit_events
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """)

            params["limit"] = query.limit
            params["offset"] = query.offset

            result = await self.db_session.execute(stmt, params)
            rows = result.fetchall()

            events = []
            for row in rows:
                event = AuditEvent(
                    id=str(row[0]),
                    event_type=AuditEventType(row[1]),
                    severity=AuditEventSeverity(row[2]),
                    entity_type=AuditEntityType(row[3]) if row[3] else None,
                    entity_id=row[4],
                    user_id=str(row[5]) if row[5] else None,
                    wallet_address=row[6],
                    session_id=row[7],
                    event_data=row[8] if isinstance(row[8], dict) else json.loads(row[8]) if row[8] else {},
                    ip_address=row[9],
                    user_agent=row[10],
                    request_method=row[11],
                    request_path=row[12],
                    request_id=row[13],
                    response_status=row[14],
                    response_time_ms=float(row[15]) if row[15] else None,
                    transaction_signature=row[16],
                    transaction_amount=float(row[17]) if row[17] else None,
                    transaction_token=row[18],
                    created_at=row[19],
                )
                events.append(event)

            return events

        except Exception as e:
            logger.error("Failed to query audit events", exc_info=True)
            return []

    async def get_user_activity(
        self,
        wallet_address: str,
        limit: int = 50,
    ) -> List[AuditEvent]:
        """Get recent activity for a wallet"""
        query = AuditQuery(
            wallet_address=wallet_address,
            limit=limit,
        )
        return await self.query_events(query)

    async def get_transaction_history(
        self,
        wallet_address: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[AuditEvent]:
        """Get transaction history for a wallet"""
        query = AuditQuery(
            wallet_address=wallet_address,
            event_type=AuditEventType.TRANSACTION_CREATED,
            start_date=start_date,
            end_date=end_date,
            limit=100,
        )
        return await self.query_events(query)

    async def get_security_events(
        self,
        wallet_address: Optional[str] = None,
        hours: int = 24,
    ) -> List[AuditEvent]:
        """Get recent security events"""
        query = AuditQuery(
            wallet_address=wallet_address,
            start_date=datetime.utcnow() - timedelta(hours=hours),
            event_type=AuditEventType.RATE_LIMIT_EXCEEDED,
        )

        # Query for multiple security event types
        if not self.db_session:
            return []

        try:
            conditions = ["severity IN ('warning', 'error', 'critical')"]
            params: Dict[str, Any] = {}

            if wallet_address:
                conditions.append("wallet_address = :wallet_address")
                params["wallet_address"] = wallet_address

            conditions.append("created_at >= :start_date")
            params["start_date"] = datetime.utcnow() - timedelta(hours=hours)

            where_clause = " AND ".join(conditions)

            from sqlalchemy import text as _text
            stmt = _text(f"""
                SELECT id, event_type, severity, entity_type, entity_id,
                       user_id, wallet_address, session_id, event_data,
                       ip_address, user_agent, request_method, request_path, request_id,
                       response_status, response_time_ms,
                       transaction_signature, transaction_amount, transaction_token,
                       created_at
                FROM chat_schema.audit_events
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT 100
            """)

            result = await self.db_session.execute(stmt, params)
            rows = result.fetchall()

            events = []
            for row in rows:
                event = AuditEvent(
                    id=str(row[0]),
                    event_type=AuditEventType(row[1]),
                    severity=AuditEventSeverity(row[2]),
                    entity_type=AuditEntityType(row[3]) if row[3] else None,
                    entity_id=row[4],
                    user_id=str(row[5]) if row[5] else None,
                    wallet_address=row[6],
                    session_id=row[7],
                    event_data=row[8] if isinstance(row[8], dict) else json.loads(row[8]) if row[8] else {},
                    ip_address=row[9],
                    user_agent=row[10],
                    request_method=row[11],
                    request_path=row[12],
                    request_id=row[13],
                    response_status=row[14],
                    response_time_ms=float(row[15]) if row[15] else None,
                    transaction_signature=row[16],
                    transaction_amount=float(row[17]) if row[17] else None,
                    transaction_token=row[18],
                    created_at=row[19],
                )
                events.append(event)

            return events

        except Exception as e:
            logger.error("Failed to get security events", exc_info=True)
            return []

    async def export_events(
        self,
        query: AuditQuery,
        format: str = "json",
    ) -> str:
        """
        Export audit events in specified format.

        Args:
            query: Query parameters
            format: Export format (json, csv)

        Returns:
            Exported data as string
        """
        events = await self.query_events(query)

        if format == "json":
            return json.dumps([e.to_dict() for e in events], indent=2, default=str)

        elif format == "csv":
            import csv
            import io

            output = io.StringIO()
            if events:
                writer = csv.DictWriter(output, fieldnames=events[0].to_dict().keys())
                writer.writeheader()
                for event in events:
                    row = event.to_dict()
                    # Convert non-serializable values
                    row['event_type'] = event.event_type.value if isinstance(event.event_type, Enum) else event.event_type
                    row['severity'] = event.severity.value if isinstance(event.severity, Enum) else event.severity
                    row['created_at'] = event.created_at.isoformat() if event.created_at else None
                    writer.writerow(row)

            return output.getvalue()

        return "[]"

    async def cleanup_old_events(self, days: int = 90) -> int:
        """
        Clean up audit events older than specified days.

        Args:
            days: Number of days to keep

        Returns:
            Number of deleted events
        """
        if not self.db_session:
            return 0

        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            from sqlalchemy import text as _text
            stmt = _text("DELETE FROM chat_schema.audit_events WHERE created_at < :cutoff_date")
            result = await self.db_session.execute(stmt, {"cutoff_date": cutoff_date})
            await self.db_session.commit()

            return result.rowcount

        except Exception as e:
            logger.error("Failed to cleanup audit events", exc_info=True)
            await self.db_session.rollback()
            return 0

    async def get_statistics(
        self,
        wallet_address: Optional[str] = None,
        days: int = 30,
    ) -> Dict[str, Any]:
        """Get audit statistics"""
        if not self.db_session:
            return {}

        try:
            from sqlalchemy import text as _text

            start_date = datetime.utcnow() - timedelta(days=days)

            if wallet_address:
                count_sql = _text(
                    "SELECT COUNT(*) FROM chat_schema.audit_events"
                    " WHERE created_at >= :start_date AND wallet_address = :wallet_address"
                )
                by_type_sql = _text(
                    "SELECT event_type, COUNT(*) FROM chat_schema.audit_events"
                    " WHERE created_at >= :start_date AND wallet_address = :wallet_address"
                    " GROUP BY event_type ORDER BY COUNT(*) DESC"
                )
                by_sev_sql = _text(
                    "SELECT severity, COUNT(*) FROM chat_schema.audit_events"
                    " WHERE created_at >= :start_date AND wallet_address = :wallet_address"
                    " GROUP BY severity"
                )
                params: Dict[str, Any] = {"start_date": start_date, "wallet_address": wallet_address}
            else:
                count_sql = _text(
                    "SELECT COUNT(*) FROM chat_schema.audit_events"
                    " WHERE created_at >= :start_date"
                )
                by_type_sql = _text(
                    "SELECT event_type, COUNT(*) FROM chat_schema.audit_events"
                    " WHERE created_at >= :start_date"
                    " GROUP BY event_type ORDER BY COUNT(*) DESC"
                )
                by_sev_sql = _text(
                    "SELECT severity, COUNT(*) FROM chat_schema.audit_events"
                    " WHERE created_at >= :start_date"
                    " GROUP BY severity"
                )
                params = {"start_date": start_date}

            total_count = (await self.db_session.execute(count_sql, params)).scalar()
            by_type = {row[0]: row[1] for row in (await self.db_session.execute(by_type_sql, params)).fetchall()}
            by_severity = {row[0]: row[1] for row in (await self.db_session.execute(by_sev_sql, params)).fetchall()}

            return {
                "total_events": total_count,
                "by_event_type": by_type,
                "by_severity": by_severity,
                "period_days": days,
            }

        except Exception as e:
            logger.error("Failed to get audit statistics", exc_info=True)
            return {}


# ============================================================================
# Helper Functions
# ============================================================================

def create_audit_event(
    event_type: AuditEventType,
    wallet_address: Optional[str] = None,
    user_id: Optional[str] = None,
    severity: AuditEventSeverity = AuditEventSeverity.INFO,
    event_data: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> AuditEvent:
    """Helper to create an audit event"""
    return AuditEvent(
        event_type=event_type,
        severity=severity,
        wallet_address=wallet_address,
        user_id=user_id,
        event_data=event_data or {},
        **kwargs,
    )


# ============================================================================
# Global Instance
# ============================================================================

_audit_service: Optional[AuditTrailService] = None


def get_audit_service(db_session: Optional[AsyncSession] = None) -> AuditTrailService:
    """Get or create audit service"""
    global _audit_service
    if _audit_service is None:
        _audit_service = AuditTrailService(db_session)
    return _audit_service
