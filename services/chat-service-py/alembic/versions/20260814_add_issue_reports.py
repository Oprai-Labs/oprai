"""Add user-submitted issue reports.

Revision ID: 20260814_add_issue_reports
Revises: 20260814_add_session_shares
Create Date: 2026-08-14

Backs the Help → Report Issue form. Before this, "Report Issue" was a link
to an unrelated GitHub repository, so nothing a user wrote about OPRAI ever
reached OPRAI.

`context` is a JSONB bag rather than columns because what is worth capturing
alongside a report changes far more often than the report itself does: the
route the user was on, the app build, the browser. Promoting any of it to a
column would mean a migration every time the answer to "what else should we
have recorded" changes.

`status` is constrained rather than free text — the admin queue filters on
it, and a typo'd status is a report that quietly disappears from every view.
"""

from alembic import op


revision = "20260814_add_issue_reports"
down_revision = "20260814_add_session_shares"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_schema.issue_reports (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            wallet      TEXT NOT NULL,
            category    TEXT NOT NULL,
            subject     TEXT NOT NULL,
            description TEXT NOT NULL,
            context     JSONB NOT NULL DEFAULT '{}'::jsonb,
            status      TEXT NOT NULL DEFAULT 'open',
            admin_note  TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_issue_reports_status
                CHECK (status IN ('open', 'in_progress', 'resolved', 'dismissed')),
            CONSTRAINT ck_issue_reports_category
                CHECK (category IN ('bug', 'feature', 'account', 'other'))
        )
        """
    )
    # The admin queue reads "open first, newest first"; the user's own page
    # reads by wallet. Two indexes, one per access path.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_issue_reports_status_created
            ON chat_schema.issue_reports (status, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_issue_reports_wallet_created
            ON chat_schema.issue_reports (wallet, created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_schema.issue_reports CASCADE;")
