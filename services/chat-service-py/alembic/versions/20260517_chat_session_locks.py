"""Add per-chat token counter and lock flag.

Revision ID: 20260517_chat_session_locks
Revises: 20260508_add_message_feedback
Create Date: 2026-05-17

Adds three columns to chat_schema.chat_sessions:

  * total_tokens   BIGINT NOT NULL DEFAULT 0
      Running sum of input + output tokens charged to this conversation.
      Bumped after every successful assistant turn.

  * is_locked      BOOLEAN NOT NULL DEFAULT FALSE
      Hard-lock flag set when the chat hits its per-chat cap. The
      frontend disables the composer permanently for any locked
      session and forces the user to start a new chat. Survives
      reload because the flag is durable in Postgres (not just
      frontend session state).

  * locked_reason  TEXT NULL
      Free-text label ("token_cap" | "message_cap" | …) explaining
      why a session was locked. Used by the UI to render the
      correct banner copy.
"""

from alembic import op


revision = "20260517_chat_session_locks"
down_revision = "20260508_add_message_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE chat_schema.chat_sessions
            ADD COLUMN IF NOT EXISTS total_tokens   BIGINT  NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS is_locked      BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS locked_reason  TEXT;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE chat_schema.chat_sessions
            DROP COLUMN IF EXISTS locked_reason,
            DROP COLUMN IF EXISTS is_locked,
            DROP COLUMN IF EXISTS total_tokens;
        """
    )
