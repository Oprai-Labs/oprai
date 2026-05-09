"""seed ingest: load local markdown/text files into oprai_blockchain_knowledge."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app.services.chunker_router import chunk_document
from app.services.deduper import content_hash
from app.services.embedder import Embedder
from app.services.qdrant_writer import ChunkPayload, QdrantWriter
from app.services.sparse import make_sparse_vector
from app.sources.base import DocumentRef, RawDocument

logger = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


async def cmd_ingest_seed(directory: str) -> None:
    path = Path(directory)
    if not path.exists():
        logger.error("Directory not found: %s", directory)
        return

    files = list(path.rglob("*.md")) + list(path.rglob("*.txt"))
    if not files:
        logger.warning("No .md or .txt files found in %s", directory)
        return

    logger.info("Found %d seed files in %s", len(files), directory)

    embedder = Embedder()
    writer = QdrantWriter()
    total_added = 0

    for fpath in files:
        doc_id = "seed." + fpath.stem.replace("-", "_").replace(" ", "_")
        body = fpath.read_text(encoding="utf-8", errors="replace")
        ct = "markdown" if fpath.suffix == ".md" else "html"

        ref = DocumentRef(doc_id=doc_id, url=f"file://{fpath.resolve()}")
        raw_doc = RawDocument(ref=ref, content_type=ct, body=body, title=fpath.stem)

        chunks = chunk_document(raw_doc)
        if not chunks:
            continue

        texts = [c.text for c in chunks]
        dense_vecs = await embedder.embed_many(texts)
        sparse_vecs = [make_sparse_vector(t) for t in texts]

        payloads = [
            ChunkPayload(
                doc_id=doc_id,
                chunk_id=c.chunk_index,
                content=c.text,
                title=fpath.stem,
                section_path=c.section_path,
                source_url=f"file://{fpath.resolve()}",
                source_type="docs",
                protocol=None,
                category="protocol_documentation",
                language="en",
                published_at=None,
                content_hash=content_hash(c.text),
                tags=["seed"],
                license="internal",
            )
            for c in chunks
        ]

        added = await writer.upsert_batch(payloads, dense_vecs, sparse_vecs)
        total_added += added
        logger.info("Ingested %s → %d chunks", fpath.name, added)

    logger.info("Seed complete. Total chunks added: %d. Embedding tokens used: %d", total_added, embedder.total_tokens)
