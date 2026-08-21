"""SQLAlchemy async engine and session factory.

pgBouncer (transaction mode) compatibility
------------------------------------------
asyncpg by default caches prepared statements per connection.  When pgBouncer
in transaction mode reassigns backend connections between requests, the next
request may land on a connection that does not have the cached prepared
statement — causing ``InvalidCachedStatementError``.

Fix: set ``statement_cache_size=0`` in asyncpg connect_args to disable the
prepared-statement cache entirely.  Queries run as simple text queries, which
is slightly slower per-call but correct and compatible with pgBouncer.

Also: ``application_name`` is sent as a startup parameter by asyncpg; pgBouncer
must be configured with ``ignore_startup_parameters = application_name`` (set in
the docker-compose pgBouncer environment).
"""

import os
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

# Matches valid PostgreSQL identifiers: letter/underscore then letters/digits/underscores.
_SCHEMA_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _build_asyncpg_url(raw_url: str) -> tuple[str, dict]:
    """Convert a DATABASE_URL to asyncpg-compatible URL and connect_args.

    asyncpg does not accept ``sslmode`` as a query parameter — it must be
    passed as ``ssl`` in connect_args instead.
    """
    url = raw_url
    # Normalise scheme
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    # Guard against double-driver
    url = url.replace("postgresql+asyncpg+asyncpg://", "postgresql+asyncpg://")

    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    # asyncpg connect_args (passed directly to asyncpg, not as URL parameters)
    connect_args: dict = {
        # CRITICAL for pgBouncer transaction mode: disables per-connection
        # prepared-statement cache so connections can be freely multiplexed.
        "statement_cache_size": 0,
        # Reasonable per-query timeout; prevents runaway queries from holding
        # a pgBouncer backend connection indefinitely.
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

def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key, "")
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


# Pool sizing.
#
# pgBouncer (transaction mode) multiplexes app connections onto a smaller pool
# of backend connections, so each replica only needs a handful of *application*
# connections. But chat-service is a streaming service: an SSE response holds
# its session for the full duration of the LLM call (tens of seconds), and a
# few concurrent users can starve a `pool_size=2 max_overflow=3` pool fast —
# we observed this live during a parallel KB ingestion run.
#
# Defaults below give 10 base + 10 overflow = 20 simultaneous sessions per
# replica. Combined with `pool_timeout` we now fail fast (1.5s) instead of
# blocking on a stalled checkout, which let upstream callers retry instead of
# piling up. Tunable per-environment via OPRAI_CHAT_DB_POOL_*.
engine = create_async_engine(
    _db_url,
    echo=False,
    pool_size=_env_int("OPRAI_CHAT_DB_POOL_SIZE", 10),
    max_overflow=_env_int("OPRAI_CHAT_DB_MAX_OVERFLOW", 10),
    pool_timeout=float(_env_int("OPRAI_CHAT_DB_POOL_TIMEOUT_MS", 1500)) / 1000.0,
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
    """FastAPI dependency that yields an async DB session.

    Commits on clean exit, rolls back on exception.  Each HTTP request
    gets its own session (one transaction per request).
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Verify database connectivity on startup and ensure the schema exists."""
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
