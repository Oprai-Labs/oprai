"""Add product-analytics event stream (analytics_schema.events).

Revision ID: 20260814_add_analytics_events
Revises: 20260814_add_llm_usage
Create Date: 2026-08-14

The capture home for product events — app opens, funnel steps
(card shown -> confirm -> submit -> confirmed/failed), feature usage and
client errors — so funnel conversion, feature adoption and activation can be
measured. `properties` is a JSONB bag (meta only, NO message content / PII);
what is worth capturing alongside an event changes far more often than the
event shape, so it stays schema-less.

Lives in its own `analytics_schema` (not chat_schema) because these events are
product-wide, not chat-specific; chat-service owns it only because it is the
DB-having service closest to the gateway that injects the wallet. The gateway
itself is a stateless proxy with no Postgres.
"""

from alembic import op


revision = "20260814_add_analytics_events"
down_revision = "20260814_add_llm_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS analytics_schema")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics_schema.events (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            wallet      TEXT NOT NULL,
            session_id  UUID,
            event_type  TEXT NOT NULL,
            event_name  TEXT NOT NULL,
            properties  JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    # Per-user journey (wallet, time); funnel/adoption (type+name, time).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_events_wallet_created "
        "ON analytics_schema.events (wallet, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_events_type_name_created "
        "ON analytics_schema.events (event_type, event_name, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_events_created "
        "ON analytics_schema.events (created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS analytics_schema.events CASCADE;")
    # Leave the schema — dropping it could hit other future analytics tables.
