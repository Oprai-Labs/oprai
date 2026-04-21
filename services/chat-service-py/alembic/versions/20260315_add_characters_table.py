"""Add characters table to chat_schema.

Revision ID: 20260315_add_characters
Revises: 20260302_soft_delete_pin
Create Date: 2026-03-15

The characters table stores AI agent personality configurations.
It was originally managed only via ORM (Base.metadata.create_all),
which broke Alembic upgrades on fresh databases because the
20260411 migration tries to create GIN indexes on this table
without it existing.  This migration canonicalises the DDL.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260315_add_characters"
down_revision = "20260302_soft_delete_pin"
branch_labels = None
depends_on = None

SCHEMA = "chat_schema"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.characters (
            id            VARCHAR(36)  PRIMARY KEY,
            name          VARCHAR(255) NOT NULL,
            model_provider VARCHAR(50) NOT NULL,
            clients       JSONB        NOT NULL DEFAULT '[]',
            bio           JSONB        NOT NULL,
            lore          JSONB,
            knowledge     JSONB,
            message_examples JSONB,
            post_examples JSONB,
            topics        JSONB,
            adjectives    JSONB,
            style         JSONB,
            settings      JSONB,
            templates     JSONB,
            system_prompt TEXT,
            active        BOOLEAN      NOT NULL DEFAULT TRUE,
            owner_wallet  VARCHAR(255),
            is_public     BOOLEAN      NOT NULL DEFAULT FALSE,
            tags          JSONB,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)

    # Basic B-tree indexes (low cardinality, used by WHERE clauses)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_characters_owner_wallet
            ON {SCHEMA}.characters (owner_wallet)
    """)

    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_characters_is_public
            ON {SCHEMA}.characters (is_public)
            WHERE is_public = TRUE
    """)

    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_characters_created_at
            ON {SCHEMA}.characters (created_at DESC)
    """)

    # Composite index for "my characters, newest first"
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_characters_owner_created
            ON {SCHEMA}.characters (owner_wallet, created_at DESC)
    """)

    # updated_at trigger — keeps the column correct for direct SQL writes too
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.update_character_timestamp()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute(f"""
        DROP TRIGGER IF EXISTS trg_characters_updated_at
            ON {SCHEMA}.characters
    """)

    op.execute(f"""
        CREATE TRIGGER trg_characters_updated_at
            BEFORE UPDATE ON {SCHEMA}.characters
            FOR EACH ROW
            EXECUTE FUNCTION {SCHEMA}.update_character_timestamp()
    """)


def downgrade() -> None:
    op.execute(f"""
        DROP TRIGGER IF EXISTS trg_characters_updated_at
            ON {SCHEMA}.characters
    """)
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.update_character_timestamp()")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.characters")
