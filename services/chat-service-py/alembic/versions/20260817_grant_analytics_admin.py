"""Grant admin_app read access to analytics_schema (for the FAZ-2 views).

analytics_schema is created and owned by chat_app at runtime, so only chat_app
(or a superuser) can hand admin_app the USAGE + SELECT it needs to build the
revenue/engagement views. init_roles.sql couldn't do this — the schema doesn't
exist yet when roles are bootstrapped. Running it here (as chat_app, the owner)
makes the grant automatic on a fresh DB instead of a manual psql step.

Idempotent: GRANT / ALTER DEFAULT PRIVILEGES are safe to re-run.

Revision ID: 20260817_grant_analytics_admin
Revises: 20260817_bump_tier_token_limits
"""
from alembic import op

# NOTE: alembic_version.version_num is varchar(32); keep revision ids <= 32 chars.
revision = "20260817_grant_analytics_admin"
down_revision = "20260817_bump_tier_token_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA analytics_schema TO admin_app")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA analytics_schema TO admin_app")
    # Future analytics tables created by chat_app are readable by admin_app too.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA analytics_schema "
        "GRANT SELECT ON TABLES TO admin_app"
    )


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA analytics_schema "
        "REVOKE SELECT ON TABLES FROM admin_app"
    )
    op.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA analytics_schema FROM admin_app")
    op.execute("REVOKE USAGE ON SCHEMA analytics_schema FROM admin_app")
