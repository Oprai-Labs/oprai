"""Add user_facts table (ChatGPT-style durable preferences).

Revision ID: 20260508_add_user_facts
Revises: 20260508_add_session_state
Create Date: 2026-05-08

Per-wallet structured preferences extracted by the post-turn LLM pass
(`services.user_facts.extract_and_upsert`) and re-injected into every
subsequent prompt via `render_for_llm`. UNIQUE(wallet, fact_type) means
each fact_type is single-valued — new extractions overwrite the old
value rather than accumulating duplicates.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260508_add_user_facts"
down_revision = "20260508_add_session_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_schema.user_facts (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            wallet        TEXT NOT NULL,
            fact_type     TEXT NOT NULL,
            fact_value    JSONB NOT NULL,
            confidence    DOUBLE PRECISION NOT NULL DEFAULT 1.0,
            source        TEXT NOT NULL DEFAULT 'extracted',
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT user_facts_wallet_type_uniq UNIQUE (wallet, fact_type),
            CONSTRAINT user_facts_confidence_range CHECK (confidence BETWEEN 0.0 AND 1.0)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS user_facts_wallet_idx
            ON chat_schema.user_facts (wallet, updated_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_schema.user_facts CASCADE;")
