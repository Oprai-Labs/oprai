"""Core ingestion pipeline: discover → fetch → chunk → embed → upsert."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IngestDocument, IngestRun, IngestSource
from app.services.chunker_router import chunk_document
from app.services.deduper import content_hash
from app.services.embedder import Embedder
from app.services.qdrant_writer import ChunkPayload, QdrantWriter
from app.services.sparse import make_sparse_vector
from app.sources.base import SourceConfig
from app.sources.docs_crawler import DocsCrawler
from app.sources.rss_poller import RSSPoller

logger = logging.getLogger(__name__)


def _get_adapter(cfg: SourceConfig):
    if cfg.source_type == "rss":
        return RSSPoller()
    return DocsCrawler()


async def run_source(cfg: SourceConfig, db: AsyncSession, run_id: int) -> None:
    """Execute a full crawl+embed+index cycle for one source."""
    adapter = _get_adapter(cfg)
    embedder = Embedder()
    writer = QdrantWriter()

    chunks_added = 0
    chunks_unchanged = 0
    docs_seen = 0
    docs_failed = 0

    async for ref in adapter.discover(cfg):
        docs_seen += 1

        # Check if doc changed (etag / hash / last_modified)
        existing = await _get_doc(db, ref.doc_id)

        raw_doc = None
        try:
            raw_doc = await adapter.fetch(ref, cfg)
        except Exception as e:
            logger.error("Fetch failed %s: %s", ref.url, e)
            docs_failed += 1
            continue

        if raw_doc is None:
            continue

        # Chunk
        chunk_results = chunk_document(raw_doc)
        if not chunk_results:
            continue

        # Process chunks
        chunk_texts = [c.text for c in chunk_results]
        chunk_hashes = [content_hash(t) for t in chunk_texts]

        # Check which chunks are unchanged
        new_texts: list[str] = []
        new_indices: list[int] = []
        for i, (text, h) in enumerate(zip(chunk_texts, chunk_hashes)):
            count = await writer.count_by_content_hash(h, ref.doc_id, i)
            if count > 0:
                chunks_unchanged += 1
            else:
                new_texts.append(text)
                new_indices.append(i)

        if not new_texts:
            continue

        # Embed new chunks
        try:
            dense_vecs = await embedder.embed_many(new_texts)
        except Exception as e:
            logger.error("Embedding failed for %s: %s", ref.doc_id, e)
            docs_failed += 1
            continue

        sparse_vecs = [make_sparse_vector(t) for t in new_texts]

        # Build payloads
        payloads: list[ChunkPayload] = []
        for local_i, (global_i, text) in enumerate(zip(new_indices, new_texts)):
            chunk_meta = chunk_results[global_i]
            published_ms = None
            if raw_doc.published_at:
                published_ms = int(raw_doc.published_at.timestamp() * 1000)

            payloads.append(ChunkPayload(
                doc_id=ref.doc_id,
                chunk_id=global_i,
                content=text,
                title=raw_doc.title,
                section_path=chunk_meta.section_path,
                source_url=ref.url,
                source_type=cfg.source_type,
                protocol=cfg.protocol,
                category=cfg.category,
                language=cfg.language,
                published_at=published_ms,
                content_hash=chunk_hashes[global_i],
                tags=cfg.tags,
                license=cfg.license,
            ))

        added = await writer.upsert_batch(payloads, dense_vecs, sparse_vecs)
        chunks_added += added

        # Delete tail chunks if doc shrank
        if existing and existing.chunk_count > len(chunk_results):
            await writer.delete_tail_chunks(ref.doc_id, len(chunk_results))

        # Update doc record
        await _upsert_doc(db, ref.doc_id, cfg.id, ref.url, chunk_hashes[0] if chunk_hashes else "", len(chunk_results))
        await db.commit()

    # Update run record
    await _update_run(db, run_id, chunks_added, chunks_unchanged, docs_seen, docs_failed, embedder.total_tokens)
    await db.commit()
    logger.info(
        "Run %d complete: source=%s docs=%d new_chunks=%d unchanged=%d",
        run_id, cfg.id, docs_seen, chunks_added, chunks_unchanged,
    )


async def _get_doc(db: AsyncSession, doc_id: str) -> Optional[IngestDocument]:
    from sqlalchemy import select
    result = await db.execute(select(IngestDocument).where(IngestDocument.doc_id == doc_id))
    return result.scalar_one_or_none()


async def _upsert_doc(
    db: AsyncSession, doc_id: str, source_id: str, url: str, ch: str, chunk_count: int
) -> None:
    from sqlalchemy import select
    result = await db.execute(select(IngestDocument).where(IngestDocument.doc_id == doc_id))
    doc = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if doc:
        doc.content_hash = ch
        doc.chunk_count = chunk_count
        doc.fetched_at = now
        doc.status = "indexed"
    else:
        db.add(IngestDocument(
            source_id=source_id,
            doc_id=doc_id,
            url=url,
            content_hash=ch,
            chunk_count=chunk_count,
            fetched_at=now,
            status="indexed",
        ))


async def _update_run(
    db: AsyncSession, run_id: int, chunks_added: int, chunks_unchanged: int,
    docs_seen: int, docs_failed: int, embedding_tokens: int,
) -> None:
    from sqlalchemy import select
    result = await db.execute(select(IngestRun).where(IngestRun.id == run_id))
    run = result.scalar_one_or_none()
    if run:
        run.finished_at = datetime.now(timezone.utc)
        run.status = "completed"
        run.chunks_added = chunks_added
        run.chunks_unchanged = chunks_unchanged
        run.docs_seen = docs_seen
        run.docs_failed = docs_failed
        run.embedding_tokens = embedding_tokens
