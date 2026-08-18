"""Add account_id to chat + analytics tables (multichain foundation, Phase 0.3).

Every wallet-keyed table gains a nullable account_id (auth_schema.users.id).
Reads stay wallet-based for now — in Phase 0 an account maps 1:1 to a wallet, so
behaviour is unchanged. The column is populated by a one-time cross-schema
backfill (run as a superuser: chat_app can't read auth_schema.linked_identities)
and by the runtime once reads switch in Phase 1.

Additive + idempotent (ADD COLUMN IF NOT EXISTS).

Revision ID: 20260818_add_account_id
Revises: 20260817_cashback_claims
"""
from alembic import op

# alembic_version.version_num is varchar(32); keep revision ids <= 32 chars.
revision = "20260818_add_account_id"
down_revision = "20260817_cashback_claims"
branch_labels = None
depends_on = None

_COLS = [
    ("chat_schema", "chat_sessions", "account_id"),
    ("chat_schema", "llm_usage", "account_id"),
    ("chat_schema", "llm_usage_daily", "account_id"),
    ("analytics_schema", "events", "account_id"),
    ("analytics_schema", "referral_codes", "account_id"),
    ("analytics_schema", "cashback_claims", "account_id"),
    ("analytics_schema", "referrals", "referrer_account_id"),
    ("analytics_schema", "referrals", "referee_account_id"),
]

_IDX = [
    ("ix_chat_sessions_account", "chat_schema.chat_sessions", "account_id"),
    ("ix_llm_usage_daily_account", "chat_schema.llm_usage_daily", "account_id"),
    ("ix_events_account", "analytics_schema.events", "account_id"),
    ("ix_cashback_claims_account", "analytics_schema.cashback_claims", "account_id"),
    ("ix_referral_codes_account", "analytics_schema.referral_codes", "account_id"),
    ("ix_referrals_referrer_account", "analytics_schema.referrals", "referrer_account_id"),
]


def upgrade() -> None:
    for schema, table, col in _COLS:
        op.execute(f"ALTER TABLE {schema}.{table} ADD COLUMN IF NOT EXISTS {col} uuid")
    for name, target, col in _IDX:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target} ({col})")


def downgrade() -> None:
    for name, target, _col in _IDX:
        op.execute(f"DROP INDEX IF EXISTS {target.split('.')[0]}.{name}")
    for schema, table, col in _COLS:
        op.execute(f"ALTER TABLE {schema}.{table} DROP COLUMN IF EXISTS {col}")
