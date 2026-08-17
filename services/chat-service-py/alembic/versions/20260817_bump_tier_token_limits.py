"""Bump tier daily LLM-token limits so tier 1 doesn't regress below the flat cap.

The original seed started tier 1 (Bronze) at 500k tokens/day — below the flat
``OPRAI_LLM_DAILY_TOKEN_CAP`` default of 1.5M every wallet already got. Now that
the cap is enforced per-tier (cost_cap.assert_under_cap), that would have *cut*
free users' allowance. Re-base the ladder at the current free allowance (1.5M)
and scale up from there, so higher tiers strictly gain and nobody loses.

Revision ID: 20260817_bump_tier_token_limits
Revises: 20260815_add_tier_referral
"""
from alembic import op

revision = "20260817_bump_tier_token_limits"
down_revision = "20260815_add_tier_referral"
branch_labels = None
depends_on = None

# tier -> (new_limit, old_limit)
_LIMITS = {
    1: (1_500_000, 500_000),
    2: (3_000_000, 1_000_000),
    3: (6_000_000, 2_000_000),
    4: (12_000_000, 4_000_000),
    5: (25_000_000, 8_000_000),
    6: (60_000_000, 20_000_000),
}


def upgrade() -> None:
    for tier, (new, _old) in _LIMITS.items():
        op.execute(
            f"UPDATE analytics_schema.tier_config SET daily_token_limit = {new} WHERE tier = {tier}"
        )


def downgrade() -> None:
    for tier, (_new, old) in _LIMITS.items():
        op.execute(
            f"UPDATE analytics_schema.tier_config SET daily_token_limit = {old} WHERE tier = {tier}"
        )
