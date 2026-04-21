"""Create memory_operations audit trail table.

Provides an immutable audit log for every vector memory operation
(insert, retrieve, delete, update, search, clear).  Required for
GDPR compliance, debugging, and capacity planning.

Revision ID: 20260412_memory_operations_audit
Revises: 20260101_initial_consent
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "20260412_memory_operations_audit"
down_revision = "20260101_initial_consent"
branch_labels = None
depends_on = None

SCHEMA = "memory_schema"


def upgrade() -> None:
    op.create_table(
        "memory_operations",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("wallet_address", sa.String(255), nullable=True),
        sa.Column("operation", sa.String(50), nullable=False),
        sa.Column("collection", sa.String(255), nullable=False),
        sa.Column("vector_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bytes_stored", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("memory_type", sa.String(100), nullable=True),
        sa.Column("metadata_", JSONB(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
        comment=(
            "Audit trail for all vector memory operations (insert, retrieve, delete). "
            "Required for GDPR compliance and debugging."
        ),
    )

    # Composite index: user-scoped queries sorted by recency (most common access pattern)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_memory_ops_user
            ON {SCHEMA}.memory_operations (user_id, created_at DESC)
    """)

    # Wallet-scoped queries (used when user_id is unavailable)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_memory_ops_wallet
            ON {SCHEMA}.memory_operations (wallet_address, created_at DESC)
    """)

    # Filter by operation type (e.g. "all deletes in the last 24h")
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_memory_ops_operation
            ON {SCHEMA}.memory_operations (operation, created_at DESC)
    """)

    # Filter by collection name
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_memory_ops_collection
            ON {SCHEMA}.memory_operations (collection, created_at DESC)
    """)

    # Time-range scans and pruning jobs
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_memory_ops_created
            ON {SCHEMA}.memory_operations (created_at DESC)
    """)

    op.execute(f"ANALYZE {SCHEMA}.memory_operations")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.idx_memory_ops_created")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.idx_memory_ops_collection")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.idx_memory_ops_operation")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.idx_memory_ops_wallet")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.idx_memory_ops_user")
    op.drop_table("memory_operations", schema=SCHEMA)
