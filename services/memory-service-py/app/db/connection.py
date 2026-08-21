"""SQLAlchemy async engine and session factory.

pgBouncer (transaction mode) compatibility
------------------------------------------
See chat-service-py/app/db/connection.py for the full explanation.
Short version: ``statement_cache_size=0`` is required to disable asyncpg's
prepared-statement cache when running behind pgBouncer in transaction mode.
"""

import re
from collections.abc import AsyncGenerator
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import sqlalchemy
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

_SCHEMA_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _build_asyncpg_url(raw_url: str) -> tuple[str, dict]:
    """Convert a DATABASE_URL to asyncpg-compatible URL and connect_args."""
    url = raw_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    url = url.replace("postgresql+asyncpg+asyncpg://", "postgresql+asyncpg://")

    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    connect_args: dict = {
        "statement_cache_size": 0,  # Required for pgBouncer transaction mode
        "command_timeout": 30,
    }

    if "sslmode" in params:
        mode = params.pop("sslmode")[0]
        if mode == "disable":
            connect_args["ssl"] = False
        elif mode in ("require", "verify-ca", "verify-full"):
            connect_args["ssl"] = True

    clean_query = urlencode({k: v[0] for k, v in params.items()})
    clean_url = urlunparse(parsed._replace(query=clean_query))
    return clean_url, connect_args


_db_url, _connect_args = _build_asyncpg_url(settings.DATABASE_URL)

engine = create_async_engine(
    _db_url,
    echo=False,
    # Memory service is mostly Qdrant; Postgres usage is light (consent only).
    pool_size=2,
    max_overflow=2,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args=_connect_args,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async DB session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Verify database connectivity on startup and ensure the schema exists.

    Table creation is handled by Alembic migrations, NOT here.
    The only idempotent DDL here is schema creation (safe to run every boot).
    """
    if not _SCHEMA_RE.match(settings.DB_SCHEMA):
        raise ValueError(
            f"Invalid DB_SCHEMA {settings.DB_SCHEMA!r}: must match [a-zA-Z_][a-zA-Z0-9_]*"
        )
    async with engine.begin() as conn:
        await conn.execute(
            sqlalchemy.text(f"CREATE SCHEMA IF NOT EXISTS {settings.DB_SCHEMA}")
        )


async def close_db() -> None:
    """Dispose of the engine connection pool."""
    await engine.dispose()
