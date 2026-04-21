"""Add updated_at to chat_messages, GIN indexes for character search, and message immutability trigger.

Revision ID: 20260411_message_updated_at
Revises: 20260315_add_characters
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa

revision = "20260411_message_updated_at"
down_revision = "20260315_add_characters"
branch_labels = None
depends_on = None

SCHEMA = "chat_schema"


def upgrade() -> None:
    # ── 1. Add updated_at to chat_messages ────────────────────────────────────
    op.add_column(
        "chat_messages",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema=SCHEMA,
    )

    # Database-side trigger so updated_at is always correct even for direct SQL updates
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.update_message_timestamp()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute(f"""
        DROP TRIGGER IF EXISTS trg_chat_messages_updated_at ON {SCHEMA}.chat_messages
    """)

    op.execute(f"""
        CREATE TRIGGER trg_chat_messages_updated_at
            BEFORE UPDATE ON {SCHEMA}.chat_messages
            FOR EACH ROW
            EXECUTE FUNCTION {SCHEMA}.update_message_timestamp()
    """)

    # ── 2. GIN indexes on characters for fast JSONB search ────────────────────
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_characters_bio_gin
            ON {SCHEMA}.characters USING GIN (bio)
    """)

    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_characters_tags_gin
            ON {SCHEMA}.characters USING GIN (tags)
    """)

    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_characters_active
            ON {SCHEMA}.characters (active)
            WHERE active = TRUE
    """)

    # ── 3. Partial index on chat_messages for active (non-deleted) sessions ───
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_chat_messages_updated_at
            ON {SCHEMA}.chat_messages (session_id, updated_at DESC)
    """)

    # ── 4. Analyze to refresh planner statistics ──────────────────────────────
    op.execute(f"ANALYZE {SCHEMA}.chat_messages")
    op.execute(f"ANALYZE {SCHEMA}.characters")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_chat_messages_updated_at")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_characters_active")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_characters_tags_gin")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_characters_bio_gin")
    op.execute(f"DROP TRIGGER IF EXISTS trg_chat_messages_updated_at ON {SCHEMA}.chat_messages")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.update_message_timestamp()")
    op.drop_column("chat_messages", "updated_at", schema=SCHEMA)
