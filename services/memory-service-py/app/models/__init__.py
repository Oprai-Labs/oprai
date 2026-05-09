"""SQLAlchemy models for the memory service.

Importing all models here ensures that Alembic's autogenerate and the
``Base.metadata.create_all()`` helper (used in tests) can discover every
table that belongs to ``memory_schema``.
"""

from app.models.consent import Base, UserConsent
from app.models.memory_operation import MemoryOperation

__all__ = [
    "Base",
    "MemoryOperation",
    "UserConsent",
]
