"""Chat service models — import all here so Alembic detects them."""

from app.models.session import Base, ChatSession  # noqa: F401
from app.models.message import ChatMessage  # noqa: F401
from app.models.summary import ChatSummary  # noqa: F401
