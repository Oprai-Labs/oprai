"""Lighter delegated agent keys (non-custodial perps).

One row per L1 (EVM) address: OPRAI's Lighter agent keypair for that account,
private half encrypted at rest. See app/models/lighter_account.py for the model
and the security model. Table lives in chat_schema (owned by chat_app), so no
cross-schema grants are needed.

Idempotent (IF NOT EXISTS) so re-runs and out-of-band creation are safe.
"""

from alembic import op

# revision identifiers
revision = "20260828_lighter_agent_keys"
down_revision = "20260823_session_account_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_schema.lighter_agent_keys (
            id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            l1_address            text NOT NULL UNIQUE,
            wallet_address        text NOT NULL,
            account_id            uuid,
            lighter_account_index integer NOT NULL,
            api_key_index         integer NOT NULL DEFAULT 250,
            agent_public_key      text NOT NULL,
            agent_private_key_enc text NOT NULL,
            status                text NOT NULL DEFAULT 'pending',
            created_at            timestamptz NOT NULL DEFAULT now(),
            updated_at            timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lighter_agent_keys_l1 "
        "ON chat_schema.lighter_agent_keys (l1_address)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_schema.lighter_agent_keys")
