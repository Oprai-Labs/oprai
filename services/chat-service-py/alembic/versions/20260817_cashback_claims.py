"""Cashback claims ledger.

Records each cashback withdrawal so the claimable balance (v_user_cashback) can
subtract what's already been paid out or is in flight. A 'pending' row reserves
the amount the instant a claim starts, so a concurrent/retry claim sees a reduced
balance and cannot double-spend; it flips to 'paid' with the signature on success
or 'failed' if the payout is rejected (failed rows do not reduce the balance).

Revision ID: 20260817_cashback_claims
Revises: 20260817_grant_analytics_admin
"""
from alembic import op

# alembic_version.version_num is varchar(32); keep revision ids <= 32 chars.
revision = "20260817_cashback_claims"
down_revision = "20260817_grant_analytics_admin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics_schema.cashback_claims (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            wallet        text        NOT NULL,
            amount_usd    numeric(20,6) NOT NULL,
            tx_signature  text,
            status        text        NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','paid','failed')),
            created_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cashback_claims_wallet "
        "ON analytics_schema.cashback_claims (wallet)"
    )
    # admin_app builds v_user_cashback over this table.
    op.execute("GRANT SELECT ON analytics_schema.cashback_claims TO admin_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS analytics_schema.cashback_claims")
