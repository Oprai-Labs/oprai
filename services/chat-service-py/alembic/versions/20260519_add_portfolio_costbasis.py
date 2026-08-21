"""Add per-wallet token cost basis tracking.

Revision ID: 20260519_add_portfolio_costbasis
Revises: 20260517_chat_session_locks
Create Date: 2026-05-19

Adds `chat_schema.wallet_token_costbasis` so the portfolio page can render
real all-time PnL per holding (`+$X (+Y%)` next to each token), matching
jup.ag/portfolio.

The cost-basis aggregator (`app/services/portfolio_analytics.py`) walks
Helius enhanced transactions, marking every wallet-side token transfer as
either a buy (cost = Birdeye historical USD at the tx slot) or a sale
(sale = same). The `last_signature` column makes the parser idempotent:
subsequent refreshes only scan signatures newer than the last processed
one, so refreshes stay cheap once the initial backfill is complete.

PnL math (computed at read time, not stored):
  avg_cost   = total_bought_usd / total_bought_amount
  realized   = total_sold_usd - avg_cost * total_sold_amount
  unrealized = (current_price - avg_cost) * current_balance
  pnl_all   = realized + unrealized
"""

from alembic import op

revision = "20260519_add_portfolio_costbasis"
down_revision = "20260517_chat_session_locks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_schema.wallet_token_costbasis (
            wallet                TEXT          NOT NULL,
            mint                  TEXT          NOT NULL,
            total_bought_amount   NUMERIC(36,18) NOT NULL DEFAULT 0,
            total_bought_usd      NUMERIC(18,6)  NOT NULL DEFAULT 0,
            total_sold_amount     NUMERIC(36,18) NOT NULL DEFAULT 0,
            total_sold_usd        NUMERIC(18,6)  NOT NULL DEFAULT 0,
            last_signature        TEXT,
            last_processed_at     TIMESTAMPTZ,
            created_at            TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            updated_at            TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            PRIMARY KEY (wallet, mint)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_costbasis_wallet
            ON chat_schema.wallet_token_costbasis (wallet);
        """
    )
    # Sync-state row per wallet so the parser knows when it last ran *for
    # this wallet* (independent of any single mint). Avoids re-walking the
    # entire signature history on every page load — we only fan out fresh
    # signatures since `last_processed_signature`.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_schema.wallet_costbasis_sync (
            wallet                       TEXT PRIMARY KEY,
            last_processed_signature     TEXT,
            last_processed_at            TIMESTAMPTZ,
            backfill_complete            BOOLEAN NOT NULL DEFAULT FALSE,
            error_count                  INTEGER NOT NULL DEFAULT 0,
            last_error                   TEXT
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_schema.wallet_costbasis_sync;")
    op.execute("DROP TABLE IF EXISTS chat_schema.wallet_token_costbasis;")
