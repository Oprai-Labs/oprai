"""Create chat_schema.audit_events table for per-event audit trail.

Revision ID: 20260417_chat_audit_events
Revises: 20260420_add_last_message_at
Create Date: 2026-04-17 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260417_chat_audit_events"
down_revision = "20260420_add_last_message_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_schema.audit_events (
            id                    UUID         NOT NULL DEFAULT gen_random_uuid(),
            event_type            VARCHAR(50)  NOT NULL,
            severity              VARCHAR(20)  NOT NULL DEFAULT 'info',
            entity_type           VARCHAR(50),
            entity_id             VARCHAR(255),
            user_id               UUID,
            wallet_address        VARCHAR(255),
            session_id            VARCHAR(255),
            event_data            JSONB        NOT NULL DEFAULT '{}',
            ip_address            VARCHAR(45),
            user_agent            TEXT,
            request_method        VARCHAR(10),
            request_path          VARCHAR(500),
            request_id            VARCHAR(255),
            response_status       INTEGER,
            response_time_ms      DECIMAL(10,2),
            transaction_signature VARCHAR(255),
            transaction_amount    DECIMAL(20,8),
            transaction_token     VARCHAR(50),
            created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at),
            CONSTRAINT valid_severity CHECK (severity IN ('debug', 'info', 'warning', 'error', 'critical'))
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_audit_user_id
            ON chat_schema.audit_events (user_id, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_audit_wallet
            ON chat_schema.audit_events (wallet_address, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_audit_event_type
            ON chat_schema.audit_events (event_type, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_audit_session_id
            ON chat_schema.audit_events (session_id, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_audit_severity
            ON chat_schema.audit_events (severity, created_at DESC)
            WHERE severity IN ('warning', 'error', 'critical')
    """)

    # Immutability trigger — audit rows cannot be modified or deleted.
    op.execute("""
        CREATE OR REPLACE FUNCTION chat_schema.audit_events_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'audit_events is immutable — rows cannot be modified or deleted (event_type: %)',
                COALESCE(OLD.event_type, '?');
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS trg_chat_audit_immutable ON chat_schema.audit_events
    """)
    op.execute("""
        CREATE TRIGGER trg_chat_audit_immutable
            BEFORE UPDATE OR DELETE ON chat_schema.audit_events
            FOR EACH ROW
            EXECUTE FUNCTION chat_schema.audit_events_immutable()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_chat_audit_immutable ON chat_schema.audit_events")
    op.execute("DROP FUNCTION IF EXISTS chat_schema.audit_events_immutable()")
    op.execute("DROP TABLE IF EXISTS chat_schema.audit_events")
