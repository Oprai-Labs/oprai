"""
Blockchain/DeFi/Solana Knowledge RAG — Read path (chat-service-py inline).

Queries the shared `oprai_blockchain_knowledge` Qdrant collection populated by
knowledge-ingestion-service.  Phase 1: dense-only search.
Phase 3: full hybrid (dense + sparse BM25 RRF + cross-encoder rerank).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Optional

from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.http.models import (
    Distance,
    HnswConfigDiff,
    PayloadSchemaType,
    QuantizationConfig,
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType,
    SparseIndexParams,
    SparseVectorParams,
    VectorParams,
    VectorsConfig,
)

from app.config import settings

logger = logging.getLogger(__name__)

COLLECTION_NAME = "oprai_blockchain_knowledge"
EMBEDDING_DIM = 3072
EMBEDDING_MODEL = "text-embedding-3-large"
UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_OID


@dataclass
class KnowledgeChunk:
    doc_id: str
    chunk_id: int
    content: str
    title: str
    section_path: str
    source_url: str
    source_type: str
    protocol: Optional[str]
    category: Optional[str]
    language: str
    published_at: Optional[int]
    token_count: int
    score: float = 0.0
    tags: list[str] = field(default_factory=list)


class _EmbeddingCache:
    """Simple LRU cache for query embeddings — avoids redundant OpenAI calls."""

    def __init__(self, maxsize: int = 1024):
        self._cache: dict[str, list[float]] = {}
        self._order: list[str] = []
        self._maxsize = maxsize

    def get(self, key: str) -> Optional[list[float]]:
        return self._cache.get(key)

    def set(self, key: str, value: list[float]) -> None:
        if key in self._cache:
            self._order.remove(key)
        elif len(self._cache) >= self._maxsize:
            oldest = self._order.pop(0)
            del self._cache[oldest]
        self._cache[key] = value
        self._order.append(key)


class RAGService:
    """
    Read path for the blockchain knowledge RAG.

    This service is intentionally stateless between requests — it connects to
    the shared Qdrant collection and returns a formatted [Knowledge Context]
    block suitable for direct inclusion as a system message.
    """

    def __init__(self, qdrant_url: Optional[str] = None) -> None:
        self._qdrant_url = qdrant_url or settings.QDRANT_URL
        self._client: Optional[AsyncQdrantClient] = None
        self._openai: Optional[AsyncOpenAI] = None
        self._embed_cache = _EmbeddingCache()

    # ── Lazy clients ─────────────────────────────────────────────────────────

    def _get_openai(self) -> AsyncOpenAI:
        if self._openai is None:
            self._openai = AsyncOpenAI(api_key=settings.OPRAI_OPENAI_API_KEY)
        return self._openai

    async def _get_qdrant(self) -> AsyncQdrantClient:
        if self._client is None:
            self._client = AsyncQdrantClient(
                url=self._qdrant_url,
                timeout=settings.QDRANT_TIMEOUT_SEC,
            )
            await self._ensure_collection()
        return self._client

    async def warmup(self) -> None:
        """Pre-load the HNSW index and quantization codebook into RAM.

        The knowledge collection is `on_disk=true` with INT8 quantization, so
        the first query after a Qdrant restart pays a 5-15s page-in cost. We
        run a tiny query at startup so the first real user request finds the
        index already hot. Failures are non-fatal — RAG falls back gracefully
        if Qdrant is unreachable.
        """
        try:
            client = await self._get_qdrant()
            await client.query_points(
                collection_name=COLLECTION_NAME,
                query=[0.0] * EMBEDDING_DIM,
                using="dense",
                limit=1,
            )
            logger.info("RAG warmup complete (collection '%s' hot)", COLLECTION_NAME)
        except Exception as exc:
            logger.warning("RAG warmup skipped: %s", exc)

    # ── Collection bootstrap ──────────────────────────────────────────────────

    async def _ensure_collection(self) -> None:
        """Idempotently create the knowledge collection and payload indexes."""
        assert self._client is not None
        try:
            await self._client.get_collection(COLLECTION_NAME)
            logger.info("Qdrant collection '%s' exists", COLLECTION_NAME)
        except (UnexpectedResponse, Exception):
            logger.info("Creating Qdrant collection '%s'", COLLECTION_NAME)
            await self._client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config={
                    "dense": VectorParams(
                        size=EMBEDDING_DIM,
                        distance=Distance.COSINE,
                        on_disk=True,
                        hnsw_config=HnswConfigDiff(m=16, ef_construct=200),
                        quantization_config=QuantizationConfig(
                            scalar=ScalarQuantization(
                                scalar=ScalarQuantizationConfig(
                                    type=ScalarType.INT8,
                                    always_ram=True,
                                )
                            )
                        ),
                    ),
                },
                sparse_vectors_config={
                    "sparse": SparseVectorParams(
                        index=SparseIndexParams(on_disk=False),
                        modifier=models.Modifier.IDF,
                    ),
                },
            )
        await self._ensure_payload_indexes()

    async def _ensure_payload_indexes(self) -> None:
        assert self._client is not None
        keyword_fields = (
            "doc_id", "source_id", "source_type", "protocol", "category",
            "language", "content_hash", "tags",
        )
        range_fields = ("published_at", "fetched_at")

        for fname in keyword_fields:
            try:
                await self._client.create_payload_index(
                    collection_name=COLLECTION_NAME,
                    field_name=fname,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception:
                pass  # already exists

        for fname in range_fields:
            try:
                await self._client.create_payload_index(
                    collection_name=COLLECTION_NAME,
                    field_name=fname,
                    field_schema=PayloadSchemaType.INTEGER,
                )
            except Exception:
                pass

    # ── Embedding ─────────────────────────────────────────────────────────────

    async def _embed(self, text: str) -> list[float]:
        cache_key = hashlib.sha256(text.encode()).hexdigest()
        cached = self._embed_cache.get(cache_key)
        if cached is not None:
            return cached

        resp = await self._get_openai().embeddings.create(
            model=EMBEDDING_MODEL,
            input=text,
            dimensions=EMBEDDING_DIM,
        )
        vec = resp.data[0].embedding
        self._embed_cache.set(cache_key, vec)
        return vec

    # ── Search ───────────────────────────────────────────────────────────────

    async def _search_dense(
        self,
        query_vec: list[float],
        top_k: int,
        language: Optional[str] = None,
    ) -> list[KnowledgeChunk]:
        client = await self._get_qdrant()

        query_filter = None
        if language and language != "en":
            query_filter = models.Filter(
                should=[
                    models.FieldCondition(key="language", match=models.MatchValue(value=language)),
                    models.FieldCondition(key="language", match=models.MatchValue(value="en")),
                ]
            )

        response = await client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vec,
            using="dense",
            limit=top_k,
            query_filter=query_filter,
            with_payload=True,
            # 0.3 was too permissive — it let weakly-related chunks (~30%
            # similarity) into the context, diluting focus and inviting the
            # model to pad answers with its own (sometimes wrong) knowledge.
            # 0.4 drops the weakest matches while keeping genuine hits.
            score_threshold=0.4,
        )

        return [self._to_chunk(r.payload or {}, r.score) for r in response.points if r.payload]

    def _to_chunk(self, payload: dict[str, Any], score: float) -> KnowledgeChunk:
        return KnowledgeChunk(
            doc_id=payload.get("doc_id", "unknown"),
            chunk_id=payload.get("chunk_id", 0),
            content=payload.get("content", ""),
            title=payload.get("title", ""),
            section_path=payload.get("section_path", ""),
            source_url=payload.get("source_url", ""),
            source_type=payload.get("source_type", "docs"),
            protocol=payload.get("protocol"),
            category=payload.get("category"),
            language=payload.get("language", "en"),
            published_at=payload.get("published_at"),
            token_count=payload.get("token_count", len(payload.get("content", "").split())),
            score=score,
            tags=payload.get("tags") or [],
        )

    # ── Diversity + budget ────────────────────────────────────────────────────

    def _apply_diversity_and_budget(
        self,
        chunks: list[KnowledgeChunk],
        top_n: int,
        token_budget: int,
    ) -> list[KnowledgeChunk]:
        seen_docs: dict[str, int] = {}
        selected: list[KnowledgeChunk] = []
        used_tokens = 0

        for chunk in chunks:
            if len(selected) >= top_n:
                break
            count = seen_docs.get(chunk.doc_id, 0)
            if count >= 2:
                continue
            if used_tokens + chunk.token_count > token_budget:
                continue
            seen_docs[chunk.doc_id] = count + 1
            used_tokens += chunk.token_count
            selected.append(chunk)

        return selected

    # ── Context formatting ────────────────────────────────────────────────────

    def _format_context_block(self, chunks: list[KnowledgeChunk]) -> str:
        if not chunks:
            return ""

        header = (
            "[Knowledge Context]\n"
            "The following reference material was retrieved from OPRAI's knowledge base. "
            "Use it to answer accurately. Do NOT fabricate information not present here.\n"
        )

        parts = [header]
        for chunk in chunks:
            ts = ""
            if chunk.published_at:
                try:
                    dt = datetime.fromtimestamp(chunk.published_at / 1000, tz=timezone.utc)
                    ts = dt.strftime("%Y-%m-%d")
                except Exception:
                    pass

            meta_parts = [p for p in [chunk.protocol, chunk.source_type, ts] if p]
            meta = " / ".join(meta_parts) if meta_parts else ""

            section = f"Section: {chunk.section_path}" if chunk.section_path else ""
            title_line = chunk.title if chunk.title else chunk.doc_id

            block = (
                f"---\n"
                + (f"{title_line}" if title_line else "")
                + (f" ({meta})" if meta else "")
                + "\n"
                + (f"{section}\n" if section else "")
                + chunk.content.strip()
                + "\n"
            )
            parts.append(block)

        return "\n".join(parts)

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_context_for_query(
        self,
        query: str,
        max_tokens: int = 1500,
    ) -> str:
        """
        Retrieve relevant blockchain knowledge chunks and return a formatted
        [Knowledge Context] system block ready for LLM injection.

        Returns empty string if no relevant chunks found or if the collection
        is unreachable — callers should treat empty as "no context available".
        """
        try:
            query_vec = await self._embed(query)
        except Exception:
            logger.warning("RAG: embedding failed", exc_info=True)
            return ""

        try:
            chunks = await self._search_dense(
                query_vec,
                top_k=settings.KNOWLEDGE_RAG_TOP_K,
            )
        except Exception:
            logger.warning("RAG: Qdrant search failed", exc_info=True)
            return ""

        top_chunks = self._apply_diversity_and_budget(
            chunks,
            top_n=settings.KNOWLEDGE_RAG_RERANK_TOP_N,
            token_budget=max_tokens,
        )

        return self._format_context_block(top_chunks)

    async def get_stats(self) -> dict[str, Any]:
        client = await self._get_qdrant()
        info = await client.get_collection(COLLECTION_NAME)
        return {
            "collection": COLLECTION_NAME,
            "points": info.points_count,
            "status": info.status.value if info.status else "unknown",
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dim": EMBEDDING_DIM,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_rag_service: Optional[RAGService] = None


def get_rag_service() -> RAGService:
    global _rag_service
    if _rag_service is None:
        _rag_service = RAGService()
    return _rag_service


# ── Stable point-ID helper (used by ingestion-service too) ───────────────────

def make_point_id(doc_id: str, chunk_id: int) -> str:
    """Deterministic UUID5 from (doc_id, chunk_id) — upsert = replace."""
    return str(uuid.uuid5(UUID_NAMESPACE, f"{doc_id}:{chunk_id}"))
