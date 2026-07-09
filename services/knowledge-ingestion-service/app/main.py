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

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
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

# Read once at module load so the toggle is visible in startup logs. The
# scheduler is meant to run only on the production deploy; local dev runs
# the service for the REST API but doesn't need the hourly burn. Toggle
# with KNOWLEDGE_CRAWLER_AUTORUN=true|false in .env.
_AUTORUN_DEFAULT = os.environ.get("ENVIRONMENT", "").lower() == "production"
AUTORUN_ENABLED = (
    os.environ.get("KNOWLEDGE_CRAWLER_AUTORUN", str(_AUTORUN_DEFAULT)).lower()
    in ("true", "1", "yes", "on")
)


async def _run_scheduled_crawl() -> None:
    """Top-level scheduler job. Imports crawl_all lazily so a missing
    OPRAI_ANTHROPIC_API_KEY in the env doesn't kill the entire service at
    import-time (crawl_all.py calls sys.exit on missing keys). Each
    invocation walks every Source; sources whose `crawl_freq` window hasn't
    elapsed since the last successful run are skipped by
    `should_skip_source` (see crawl_all.py) — that's the whole point of the
    freq system. Concurrent invocations are blocked by APScheduler's
    `max_instances=1` so a long crawl can't overlap with the next tick.
    """
    try:
        # Lazy import — the module top-level reads ANTHROPIC_KEY and calls
        # sys.exit on absence; importing inside the job means missing keys
        # only break the crawl, not the whole FastAPI process.
        import crawl_all  # type: ignore[import-not-found]
    except SystemExit:
        logger.error("crawl_all import failed: API keys missing — scheduler tick skipped")
        return
    except Exception as e:
        logger.error("crawl_all import failed: %s", e)
        return

    logger.info("[scheduler] Crawl tick starting")
    try:
        await crawl_all.main(groups=set(), dry_run=False)
        logger.info("[scheduler] Crawl tick done")
    except Exception as e:
        logger.error("[scheduler] Crawl tick failed: %s", e, exc_info=True)


# ── Startup / shutdown ────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create schema + tables
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS ingestion_schema"))
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Knowledge Ingestion Service started on port %d", settings.PORT)

    scheduler: Optional[AsyncIOScheduler] = None
    if AUTORUN_ENABLED:
        # Top-of-the-hour crawl. Each Source uses its own `crawl_freq` so
        # the bulk of the 190 weekly sources do nothing 6 days out of 7 —
        # only the news feeds (freq=hourly) actually fetch every tick.
        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(
            _run_scheduled_crawl,
            CronTrigger(minute=0),
            id="crawl_all_hourly",
            max_instances=1,   # never overlap; if a tick is still running, the next is dropped
            coalesce=True,     # if we missed several ticks (deploy/restart), merge into one
            misfire_grace_time=60 * 30,  # accept ticks up to 30 min late
        )
        scheduler.start()
        logger.info("[scheduler] hourly crawler scheduled (cron: minute=0)")
    else:
        logger.info("[scheduler] autorun disabled (set KNOWLEDGE_CRAWLER_AUTORUN=true to enable)")

    yield

    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("[scheduler] shut down")
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
