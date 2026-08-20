"""Add chain to cashback_claims — per-chain claiming.

Each chain's rewards are claimed separately (min $5 per chain), paid in that
chain's native token. Existing claims are Solana.

Revision ID: 20260820_claim_chain
Revises: 20260818_add_account_id
"""

from alembic import op

revision = "20260820_claim_chain"
down_revision = "20260818_add_account_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE analytics_schema.cashback_claims "
        "ADD COLUMN IF NOT EXISTS chain VARCHAR(50) NOT NULL DEFAULT 'solana'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cashback_claims_chain "
        "ON analytics_schema.cashback_claims (account_id, chain)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS analytics_schema.ix_cashback_claims_chain")
    op.execute("ALTER TABLE analytics_schema.cashback_claims DROP COLUMN IF EXISTS chain")
