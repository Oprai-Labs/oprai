"""Add message_feedback table (per-message thumbs up/down).

Revision ID: 20260508_add_message_feedback
Revises: 20260508_add_user_facts
Create Date: 2026-05-08

Backs the F21 thumbs UI in MessageList. UNIQUE(message_id, wallet) so a
re-vote overwrites the previous one rather than inserting a duplicate.
Aggregated reads are by message_id; per-wallet history is rare so we
optimise for the common path with a single composite index.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260508_add_message_feedback"
down_revision = "20260508_add_user_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_schema.message_feedback (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            message_id  UUID NOT NULL,
            wallet      TEXT NOT NULL,
            rating      SMALLINT NOT NULL,
            comment     TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT message_feedback_msg_wallet_uniq UNIQUE (message_id, wallet),
            CONSTRAINT message_feedback_rating_range   CHECK (rating IN (-1, 1))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS message_feedback_msg_idx
            ON chat_schema.message_feedback (message_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_schema.message_feedback CASCADE;")
