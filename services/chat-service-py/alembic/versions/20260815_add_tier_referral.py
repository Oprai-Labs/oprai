"""Add tier config + referral tables (FAZ-3 rewards).

Revision ID: 20260815_add_tier_referral
Revises: 20260814_add_analytics_events
Create Date: 2026-08-15

Backs the tier/points/referral system. tier_config is a small editable table
(thresholds + per-tier perks) so tiers are tuned with an UPDATE, not a deploy.
referral_codes maps a wallet to its shareable code; referrals records who
referred whom (one referrer per referee).

Volume/points are COMPUTED from the economics rollups via admin_schema views
(v_user_tier, v_user_points) — nothing here duplicates that. Referral
attribution is via an explicit "redeem code" action, NOT a signup hook, so this
never touches the auth/sign flow.

Seeded tier thresholds are a starting point — adjust freely:
  UPDATE analytics_schema.tier_config SET min_volume_usd=... WHERE tier=...;
"""

from alembic import op

revision = "20260815_add_tier_referral"
down_revision = "20260814_add_analytics_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS analytics_schema")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics_schema.tier_config (
            tier               SMALLINT PRIMARY KEY CHECK (tier BETWEEN 1 AND 6),
            min_volume_usd     NUMERIC(20,2) NOT NULL,   -- lifetime confirmed volume to reach this tier
            daily_token_limit  BIGINT        NOT NULL,   -- LLM daily token cap for this tier (enforcement later)
            points_multiplier  NUMERIC(4,2)  NOT NULL DEFAULT 1.0,
            fee_bps_override    SMALLINT,                -- NULL = use default fee; set later to reward high tiers
            updated_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        INSERT INTO analytics_schema.tier_config
            (tier, min_volume_usd, daily_token_limit, points_multiplier) VALUES
            (1,          0,   500000, 1.00),
            (2,       1000,  1000000, 1.10),
            (3,      10000,  2000000, 1.25),
            (4,      50000,  4000000, 1.50),
            (5,     250000,  8000000, 1.75),
            (6,    1000000, 20000000, 2.00)
        ON CONFLICT (tier) DO NOTHING
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics_schema.referral_codes (
            wallet      TEXT PRIMARY KEY,
            code        TEXT NOT NULL UNIQUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics_schema.referrals (
            referee_wallet   TEXT PRIMARY KEY,          -- a user can be referred only once
            referrer_wallet  TEXT NOT NULL,
            code             TEXT NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_referrals_not_self CHECK (referee_wallet <> referrer_wallet)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_referrals_referrer "
        "ON analytics_schema.referrals (referrer_wallet)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS analytics_schema.referrals CASCADE;")
    op.execute("DROP TABLE IF EXISTS analytics_schema.referral_codes CASCADE;")
    op.execute("DROP TABLE IF EXISTS analytics_schema.tier_config CASCADE;")
