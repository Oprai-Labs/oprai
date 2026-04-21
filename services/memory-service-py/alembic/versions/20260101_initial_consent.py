"""Create memory_schema and user_consents table.

Replaces the former Base.metadata.create_all() call in connection.py.
This is now the single source of truth for the memory service schema.

Revision ID: 20260101_initial_consent
Revises: (none — first migration)
Create Date: 2026-01-01
"""

from alembic import op
import sqlalchemy as sa

revision = "20260101_initial_consent"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "memory_schema"


def upgrade() -> None:
    # Schema is created by connection.init_db() on startup (idempotent).
    # We create it here too so the migration is fully self-contained.
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    op.create_table(
        "user_consents",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("position", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("contract", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("strategy", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("preference", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("decision", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("user_id"),
        schema=SCHEMA,
    )

    # Auto-refresh updated_at on every UPDATE
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.update_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute(f"""
        DROP TRIGGER IF EXISTS trg_consents_updated_at
            ON {SCHEMA}.user_consents
    """)

    op.execute(f"""
        CREATE TRIGGER trg_consents_updated_at
            BEFORE UPDATE ON {SCHEMA}.user_consents
            FOR EACH ROW
            EXECUTE FUNCTION {SCHEMA}.update_updated_at()
    """)

    # Index: look up a single user's consent record cheaply
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_user_consents_user_id
            ON {SCHEMA}.user_consents (user_id)
    """)

    op.execute(f"ANALYZE {SCHEMA}.user_consents")


def downgrade() -> None:
    op.execute(f"""
        DROP TRIGGER IF EXISTS trg_consents_updated_at
            ON {SCHEMA}.user_consents
    """)
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.update_updated_at()")
    op.drop_table("user_consents", schema=SCHEMA)
