"""Add account_id to chat_sessions for account-scoped chat history.

One multichain account can link many wallets; chat history should follow the
account, not a single wallet, so switching to a linked wallet still shows the
same conversations. Sessions created before accounts existed have a NULL
account_id and stay wallet-scoped (see services/session.py _owner_where).

Idempotent: the column already exists on some environments (added out-of-band),
so this uses IF NOT EXISTS and backfills from auth_schema.linked_identities.
"""

from alembic import op

# revision identifiers
revision = "20260823_session_account_id"
down_revision = "20260820_claim_chain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE chat_schema.chat_sessions "
        "ADD COLUMN IF NOT EXISTS account_id uuid"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_sessions_account "
        "ON chat_schema.chat_sessions (account_id, updated_at DESC)"
    )
    # Backfill: map each session's wallet to the account that owns it.
    op.execute(
        "UPDATE chat_schema.chat_sessions cs "
        "SET account_id = li.account_id "
        "FROM auth_schema.linked_identities li "
        "WHERE li.identifier = cs.wallet_address AND cs.account_id IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS chat_schema.ix_chat_sessions_account")
    op.execute("ALTER TABLE chat_schema.chat_sessions DROP COLUMN IF EXISTS account_id")
