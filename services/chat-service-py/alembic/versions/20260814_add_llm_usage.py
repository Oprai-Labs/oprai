"""Add per-LLM-call token/cost ledger + daily rollup + model pricing.

Revision ID: 20260814_add_llm_usage
Revises: 20260814_add_issue_reports
Create Date: 2026-08-14

Persists what only lived in ephemeral Redis counters before: per-user, per-model
token usage and its DOLLAR cost. `llm_usage` is the event-level source of truth
(one row per LLM call), `llm_usage_daily` the fast rollup for dashboards, and
`model_pricing` the $/1M rates that turn tokens into cost — a table, not a
constant, because model prices change and we do not want a migration each time.

Cost is computed at write time from `model_pricing` and frozen on the row, so a
later price change never rewrites history. `is_estimated` marks rows whose token
counts came from a char/4 approximation (the default Anthropic responder path
does not yet surface exact usage) rather than a provider `usage` object.

NOTE: the seeded prices are best-effort and MUST be verified against current
provider pricing — update `model_pricing` (a plain UPDATE) when they change.
"""

from alembic import op

revision = "20260814_add_llm_usage"
down_revision = "20260814_add_issue_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_schema.model_pricing (
            model              TEXT PRIMARY KEY,
            input_usd_per_1m   NUMERIC(12,4) NOT NULL,
            output_usd_per_1m  NUMERIC(12,4) NOT NULL,
            cached_usd_per_1m  NUMERIC(12,4) NOT NULL DEFAULT 0,
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    # Best-effort seed prices ($/1M tokens). VERIFY + update to real rates.
    op.execute(
        """
        INSERT INTO chat_schema.model_pricing (model, input_usd_per_1m, output_usd_per_1m, cached_usd_per_1m) VALUES
            ('claude-sonnet-5',   3.0000, 15.0000, 0.3000),
            ('claude-haiku-4-5',  1.0000,  5.0000, 0.1000),
            ('gpt-5.4-nano',      0.0500,  0.4000, 0.0050),
            ('gpt-5.4-mini',      0.2500,  2.0000, 0.0250),
            ('gpt-5.4',           1.2500, 10.0000, 0.1250),
            ('gpt-4o-mini',       0.1500,  0.6000, 0.0750)
        ON CONFLICT (model) DO NOTHING
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_schema.llm_usage (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            wallet            TEXT NOT NULL,
            session_id        UUID,
            model             TEXT NOT NULL,
            request_kind      TEXT NOT NULL DEFAULT 'responder',
            prompt_tokens     INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens     INTEGER NOT NULL DEFAULT 0,
            total_tokens      INTEGER NOT NULL DEFAULT 0,
            cost_usd          NUMERIC(12,6),
            is_estimated      BOOLEAN NOT NULL DEFAULT FALSE,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_llm_usage_kind
                CHECK (request_kind IN ('responder', 'intent', 'tool', 'summary', 'title'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_llm_usage_wallet_created "
        "ON chat_schema.llm_usage (wallet, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_llm_usage_model_created "
        "ON chat_schema.llm_usage (model, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_llm_usage_created "
        "ON chat_schema.llm_usage (created_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_schema.llm_usage_daily (
            wallet            TEXT NOT NULL,
            stat_date         DATE NOT NULL,
            model             TEXT NOT NULL,
            prompt_tokens     BIGINT NOT NULL DEFAULT 0,
            completion_tokens BIGINT NOT NULL DEFAULT 0,
            cached_tokens     BIGINT NOT NULL DEFAULT 0,
            requests          BIGINT NOT NULL DEFAULT 0,
            cost_usd          NUMERIC(14,6) NOT NULL DEFAULT 0,
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (wallet, stat_date, model)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_llm_usage_daily_date "
        "ON chat_schema.llm_usage_daily (stat_date DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_schema.llm_usage CASCADE;")
    op.execute("DROP TABLE IF EXISTS chat_schema.llm_usage_daily CASCADE;")
    op.execute("DROP TABLE IF EXISTS chat_schema.model_pricing CASCADE;")
