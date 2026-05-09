"""SQLAlchemy ORM models for crawl-state tracking."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Column, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class IngestSource(Base):
    """Registered crawl source (one row per docs site / RSS feed / GitHub repo)."""

    __tablename__ = "ingest_sources"
    __table_args__ = {"schema": "ingestion_schema"}

    id = Column(String(128), primary_key=True)          # e.g. "solana_docs"
    source_type = Column(String(32), nullable=False)    # docs/rss/github/cookbook/defillama/governance
    base_url = Column(Text, nullable=False)
    protocol = Column(String(64))                       # canonical protocol id or NULL
    category = Column(String(64), nullable=False)
    license = Column(String(64), nullable=False, default="proprietary-fair-use")
    language = Column(String(8), nullable=False, default="en")
    schedule_cron = Column(String(64), nullable=False)
    crawl_delay_s = Column(Float, nullable=False, default=1.0)
    max_pages = Column(Integer, nullable=False, default=5000)
    enabled = Column(Integer, nullable=False, default=1)
    legal_review_passed = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class IngestDocument(Base):
    """One row per discovered URL / feed item."""

    __tablename__ = "ingest_documents"
    __table_args__ = {"schema": "ingestion_schema"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(String(128), nullable=False, index=True)
    doc_id = Column(String(256), nullable=False, unique=True, index=True)
    url = Column(Text, nullable=False)
    content_hash = Column(String(64), index=True)       # sha256 of normalised content
    chunk_count = Column(Integer, default=0)
    etag = Column(String(256))
    last_modified = Column(String(64))
    fetched_at = Column(DateTime(timezone=True))
    published_at = Column(DateTime(timezone=True))
    status = Column(String(32), default="pending")      # pending/indexed/failed/skipped
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class IngestRun(Base):
    """One row per crawl run of a source."""

    __tablename__ = "ingest_runs"
    __table_args__ = {"schema": "ingestion_schema"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(String(128), nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    finished_at = Column(DateTime(timezone=True))
    status = Column(String(32), default="running")      # running/completed/failed
    docs_seen = Column(Integer, default=0)
    chunks_added = Column(Integer, default=0)
    chunks_unchanged = Column(Integer, default=0)
    docs_failed = Column(Integer, default=0)
    embedding_tokens = Column(BigInteger, default=0)
    error = Column(Text)
