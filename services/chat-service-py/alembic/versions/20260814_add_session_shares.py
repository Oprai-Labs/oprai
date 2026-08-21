"""Add public read-only share links for chat sessions.

Revision ID: 20260814_add_session_shares
Revises: 20260519_add_portfolio_costbasis
Create Date: 2026-08-14

Backs `POST /sessions/{id}/share` and the unauthenticated
`GET /public/shares/{token}`: a chat can be published behind an opaque
token that anyone can read without connecting a wallet, and that nobody
can find without the link.

Two columns carry the privacy guarantees:

- `shared_up_to` is the snapshot cutoff. The public endpoint returns only
  messages created at or before it, so turns written AFTER sharing stay
  private until the owner refreshes the link. Without it, sharing once
  would publish the conversation forever forward.
- `title` is copied at share time, because renaming a chat afterwards is a
  private act that must not rewrite what visitors already see.

`UNIQUE (session_id)` keeps one live link per chat — re-sharing refreshes
the row and keeps the token, so an already-distributed URL keeps working.
Revocation DELETEs the row, which is the only revocation a user would
recognise as one; the FK cascade does the same when a chat is hard-deleted.
"""

from alembic import op

revision = "20260814_add_session_shares"
down_revision = "20260519_add_portfolio_costbasis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_schema.chat_session_shares (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id     UUID NOT NULL
                             REFERENCES chat_schema.chat_sessions(id) ON DELETE CASCADE,
            wallet_address TEXT NOT NULL,
            token          VARCHAR(64) NOT NULL,
            shared_up_to   TIMESTAMPTZ NOT NULL,
            title          TEXT NOT NULL DEFAULT 'Shared chat',
            view_count     INTEGER NOT NULL DEFAULT 0,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_session_shares_session UNIQUE (session_id)
        )
        """
    )
    # Every public read is a lookup by token — this index is the hot path,
    # and UNIQUE is what makes a collision impossible rather than unlikely.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_session_shares_token
            ON chat_schema.chat_session_shares (token)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_session_shares_wallet
            ON chat_schema.chat_session_shares (wallet_address, created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_schema.chat_session_shares CASCADE;")
