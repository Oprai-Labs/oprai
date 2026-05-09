"""Add session_state JSONB column to chat_sessions.

Revision ID: 20260508_add_session_state
Revises: 20260420_add_last_message_at
Create Date: 2026-05-08

Stores a structured snapshot of the user's current intent, candidate entities,
selected entity, pending decision, wallet snapshot and durable blockers. The
chat-service updates it after every assistant turn (via
`services.session_state.update_session_state`) and re-injects the rendered
form on every subsequent turn (`render_session_state_for_llm`).

This survives block summarisation, which is the primary defence against
"the responder lost the candidate pool list when the conversation crossed
100 messages" failure mode we saw in production.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260508_add_session_state"
down_revision = "20260417_chat_audit_events"
branch_labels = None
depends_on = None

SCHEMA = "chat_schema"


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column(
            "session_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=SCHEMA,
    )
    op.execute(f"""
        COMMENT ON COLUMN {SCHEMA}.chat_sessions.session_state IS
        'Persisted high-level state extracted by the responder after each turn: '
        'current intent, active protocol, candidate entities, pending decisions. '
        'Survives summarisation and is injected as a single system message every turn.'
    """)


def downgrade() -> None:
    op.drop_column("chat_sessions", "session_state", schema=SCHEMA)
