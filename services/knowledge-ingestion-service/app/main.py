"""Knowledge Ingestion Service — FastAPI application.

Admin REST API for managing crawl sources, triggering runs, and monitoring
ingestion state.  Port 3070.

Sources are configured via source_configs/*.yaml and can also be managed
via the REST API at runtime.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import AsyncSessionLocal, Base, IngestRun, IngestSource, engine, get_db
from app.services.pipeline import run_source
from app.sources.base import SourceConfig

logger = logging.getLogger(__name__)

# ── Startup / shutdown ────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create schema + tables
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS ingestion_schema"))
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Knowledge Ingestion Service started on port %d", settings.PORT)
    yield
    logger.info("Knowledge Ingestion Service shutting down")


app = FastAPI(
    title="OPRAI Knowledge Ingestion Service",
    version="0.1.0",
    lifespan=lifespan,
)

origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()] or [
    "http://localhost:3000",
    "http://localhost:4200",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pydantic models ───────────────────────────────────────────────────────────


class SourceCreate(BaseModel):
    id: str
    source_type: str
    base_url: str
    protocol: Optional[str] = None
    category: str
    license: str = "proprietary-fair-use"
    language: str = "en"
    schedule_cron: str = "0 3 * * *"
    crawl_delay_s: float = 1.0
    max_pages: int = 5000
    tags: list[str] = []
    extra: dict[str, Any] = {}


class SourceResponse(BaseModel):
    id: str
    source_type: str
    base_url: str
    protocol: Optional[str]
    category: str
    enabled: bool
    created_at: datetime


class RunResponse(BaseModel):
    id: int
    source_id: str
    started_at: datetime
    finished_at: Optional[datetime]
    status: str
    docs_seen: int
    chunks_added: int
    chunks_unchanged: int
    docs_failed: int
    embedding_tokens: int


# ── Running jobs registry (in-memory for single-instance) ─────────────────────
_running_jobs: dict[str, asyncio.Task] = {}


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok", "service": "knowledge-ingestion-service"}


@app.post("/sources", response_model=SourceResponse, status_code=201)
async def create_source(payload: SourceCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.get(IngestSource, payload.id)
    if existing:
        raise HTTPException(400, f"Source '{payload.id}' already exists")

    src = IngestSource(
        id=payload.id,
        source_type=payload.source_type,
        base_url=payload.base_url,
        protocol=payload.protocol,
        category=payload.category,
        license=payload.license,
        language=payload.language,
        schedule_cron=payload.schedule_cron,
        crawl_delay_s=payload.crawl_delay_s,
        max_pages=payload.max_pages,
        enabled=1,
    )
    db.add(src)
    await db.commit()
    await db.refresh(src)
    return SourceResponse(
        id=src.id,
        source_type=src.source_type,
        base_url=src.base_url,
        protocol=src.protocol,
        category=src.category,
        enabled=bool(src.enabled),
        created_at=src.created_at,
    )


@app.get("/sources")
async def list_sources(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(IngestSource).order_by(IngestSource.id))
    sources = result.scalars().all()
    return [
        {
            "id": s.id,
            "source_type": s.source_type,
            "base_url": s.base_url,
            "protocol": s.protocol,
            "category": s.category,
            "enabled": bool(s.enabled),
            "schedule_cron": s.schedule_cron,
        }
        for s in sources
    ]


@app.post("/sources/{source_id}/run", response_model=RunResponse)
async def trigger_run(
    source_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    src = await db.get(IngestSource, source_id)
    if not src:
        raise HTTPException(404, f"Source '{source_id}' not found")

    if source_id in _running_jobs and not _running_jobs[source_id].done():
        raise HTTPException(409, f"Source '{source_id}' already running")

    run = IngestRun(source_id=source_id)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    cfg = SourceConfig(
        id=src.id,
        source_type=src.source_type,  # type: ignore[arg-type]
        base_url=src.base_url,
        protocol=src.protocol,
        category=src.category,
        license=src.license,
        language=src.language,
        schedule_cron=src.schedule_cron,
        crawl_delay_s=src.crawl_delay_s,
        max_pages=src.max_pages,
    )

    async def _run():
        async with AsyncSessionLocal() as session:
            await run_source(cfg, session, run.id)

    task = asyncio.create_task(_run())
    _running_jobs[source_id] = task

    return RunResponse(
        id=run.id,
        source_id=run.source_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status,
        docs_seen=run.docs_seen or 0,
        chunks_added=run.chunks_added or 0,
        chunks_unchanged=run.chunks_unchanged or 0,
        docs_failed=run.docs_failed or 0,
        embedding_tokens=run.embedding_tokens or 0,
    )


@app.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: int, db: AsyncSession = Depends(get_db)):
    run = await db.get(IngestRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return RunResponse(
        id=run.id,
        source_id=run.source_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status,
        docs_seen=run.docs_seen or 0,
        chunks_added=run.chunks_added or 0,
        chunks_unchanged=run.chunks_unchanged or 0,
        docs_failed=run.docs_failed or 0,
        embedding_tokens=run.embedding_tokens or 0,
    )


@app.get("/admin/stats")
async def admin_stats(db: AsyncSession = Depends(get_db)):
    sources_result = await db.execute(select(IngestSource))
    sources = sources_result.scalars().all()

    stats = []
    for s in sources:
        # Latest run
        run_result = await db.execute(
            select(IngestRun)
            .where(IngestRun.source_id == s.id)
            .order_by(IngestRun.started_at.desc())
            .limit(1)
        )
        run = run_result.scalar_one_or_none()
        stats.append({
            "id": s.id,
            "source_type": s.source_type,
            "enabled": bool(s.enabled),
            "last_success_at": run.finished_at.isoformat() if run and run.status == "completed" else None,
            "last_run_status": run.status if run else None,
        })

    return {"sources": stats, "total": len(stats)}


@app.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    from app.services.qdrant_writer import QdrantWriter
    writer = QdrantWriter()
    from qdrant_client.http import models
    client = writer._get_client()
    await client.delete(
        collection_name="oprai_blockchain_knowledge",
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
            )
        ),
    )
    return {"deleted": doc_id}
