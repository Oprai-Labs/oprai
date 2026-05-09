"""Add soft-delete and pin columns to chat_sessions.

Revision ID: 20260302_soft_delete_pin
Revises: 20260208_summaries
Create Date: 2026-03-02
"""

from alembic import op
import sqlalchemy as sa

revision = "20260302_soft_delete_pin"
down_revision = "20260208_summaries"
branch_labels = None
depends_on = None

SCHEMA = "chat_schema"


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        schema=SCHEMA,
    )
    op.add_column(
        "chat_sessions",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "chat_sessions",
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default="false"),
        schema=SCHEMA,
    )
    op.add_column(
        "chat_sessions",
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chat_sessions_wallet_active",
        "chat_sessions",
        ["wallet_address", "is_deleted", "updated_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_wallet_active", table_name="chat_sessions", schema=SCHEMA)
    op.drop_column("chat_sessions", "pinned_at", schema=SCHEMA)
    op.drop_column("chat_sessions", "pinned", schema=SCHEMA)
    op.drop_column("chat_sessions", "deleted_at", schema=SCHEMA)
    op.drop_column("chat_sessions", "is_deleted", schema=SCHEMA)
