"""Add chat_summaries table and message_count column.

Revision ID: 20260208_summaries
Revises:
Create Date: 2026-02-08
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260208_summaries"
down_revision = "20260101_initial"
branch_labels = None
depends_on = None

SCHEMA = "chat_schema"


def upgrade() -> None:
    # Schema is guaranteed to exist from the initial migration.
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # Add message_count to chat_sessions (IF NOT EXISTS guards against re-runs).
    op.execute(f"""
        ALTER TABLE {SCHEMA}.chat_sessions
        ADD COLUMN IF NOT EXISTS message_count INTEGER NOT NULL DEFAULT 0
    """)

    # Backfill message_count from existing messages
    op.execute(f"""
        UPDATE {SCHEMA}.chat_sessions s
        SET message_count = (
            SELECT COUNT(*)
            FROM {SCHEMA}.chat_messages m
            WHERE m.session_id = s.id
        )
    """)

    # Create chat_summaries table
    op.create_table(
        "chat_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("block_index", sa.Integer(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("message_start", sa.Integer(), nullable=False),
        sa.Column("message_end", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("session_id", "block_index", name="uq_summary_session_block"),
        schema=SCHEMA,
    )

    op.create_index(
        "ix_chat_summaries_session_block",
        "chat_summaries",
        ["session_id", "block_index"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_summaries_session_block", table_name="chat_summaries", schema=SCHEMA)
    op.drop_table("chat_summaries", schema=SCHEMA)
    op.drop_column("chat_sessions", "message_count", schema=SCHEMA)
