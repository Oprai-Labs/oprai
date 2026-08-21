"""Create initial chat_sessions and chat_messages tables.

This is the root migration — all subsequent migrations depend on these tables.
Without this, the first incremental migration (add_summaries) would fail on a
fresh database because it tries to ADD COLUMN to a non-existent table.

Revision ID: 20260101_initial
Revises:
Create Date: 2026-01-01
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260101_initial"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "chat_schema"


def upgrade() -> None:
    # Ensure schema exists
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # ── chat_sessions ─────────────────────────────────────────────────────────
    # Base columns only — incremental migrations add: message_count,
    # is_deleted/deleted_at/pinned/pinned_at, updated_at trigger.
    op.create_table(
        "chat_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("wallet_address", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False, server_default="New chat"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema=SCHEMA,
    )

    op.create_index(
        "ix_chat_sessions_wallet_created",
        "chat_sessions",
        ["wallet_address", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chat_sessions_user_id",
        "chat_sessions",
        ["user_id"],
        schema=SCHEMA,
    )

    # ── chat_messages ─────────────────────────────────────────────────────────
    # updated_at is added by the 20260411 migration (message_updated_at_and_indexes).
    op.create_table(
        "chat_messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("wallet_address", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, comment="user | assistant | system"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema=SCHEMA,
    )

    op.create_index(
        "ix_chat_messages_session_created",
        "chat_messages",
        ["session_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chat_messages_wallet_address",
        "chat_messages",
        ["wallet_address"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_wallet_address", table_name="chat_messages", schema=SCHEMA)
    op.drop_index("ix_chat_messages_session_created", table_name="chat_messages", schema=SCHEMA)
    op.drop_table("chat_messages", schema=SCHEMA)
    op.drop_index("ix_chat_sessions_user_id", table_name="chat_sessions", schema=SCHEMA)
    op.drop_index("ix_chat_sessions_wallet_created", table_name="chat_sessions", schema=SCHEMA)
    op.drop_table("chat_sessions", schema=SCHEMA)
