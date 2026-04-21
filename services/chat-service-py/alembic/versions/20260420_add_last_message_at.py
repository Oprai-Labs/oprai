"""Add last_message_at to chat_sessions for fast "most recent active session" queries.

Revision ID: 20260420_add_last_message_at
Revises: 20260411_message_updated_at
Create Date: 2026-04-20
"""

from alembic import op
import sqlalchemy as sa

revision = "20260420_add_last_message_at"
down_revision = "20260411_message_updated_at"
branch_labels = None
depends_on = None

SCHEMA = "chat_schema"


def upgrade() -> None:
    # ── 1. Add last_message_at column ─────────────────────────────────────────
    op.add_column(
        "chat_sessions",
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema=SCHEMA,
    )

    # Back-fill from chat_messages so existing sessions are correct
    op.execute(f"""
        UPDATE {SCHEMA}.chat_sessions s
        SET last_message_at = (
            SELECT MAX(created_at)
            FROM {SCHEMA}.chat_messages m
            WHERE m.session_id = s.id
        )
    """)

    # ── 2. Trigger: keep last_message_at current on every new message ─────────
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.update_session_last_message_at()
        RETURNS TRIGGER AS $$
        BEGIN
            UPDATE {SCHEMA}.chat_sessions
            SET last_message_at = NEW.created_at
            WHERE id = NEW.session_id
              AND (last_message_at IS NULL OR last_message_at < NEW.created_at);
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute(f"""
        DROP TRIGGER IF EXISTS trg_update_session_last_message
            ON {SCHEMA}.chat_messages
    """)

    op.execute(f"""
        CREATE TRIGGER trg_update_session_last_message
            AFTER INSERT ON {SCHEMA}.chat_messages
            FOR EACH ROW
            EXECUTE FUNCTION {SCHEMA}.update_session_last_message_at()
    """)

    # ── 3. Composite index: user's sessions sorted by most recent activity ────
    # Replaces or augments any existing idx_sessions_user_id index.
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_chat_sessions_user_last_message
            ON {SCHEMA}.chat_sessions (user_id, last_message_at DESC NULLS LAST)
            WHERE is_deleted = FALSE
    """)

    # ── 4. Refresh planner stats ──────────────────────────────────────────────
    op.execute(f"ANALYZE {SCHEMA}.chat_sessions")


def downgrade() -> None:
    op.execute(f"""
        DROP TRIGGER IF EXISTS trg_update_session_last_message
            ON {SCHEMA}.chat_messages
    """)
    op.execute(f"""
        DROP FUNCTION IF EXISTS {SCHEMA}.update_session_last_message_at()
    """)
    op.execute(f"""
        DROP INDEX IF EXISTS {SCHEMA}.ix_chat_sessions_user_last_message
    """)
    op.drop_column("chat_sessions", "last_message_at", schema=SCHEMA)
