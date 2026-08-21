"""
HybridRetriever — Phase 1: dense-only wrapper around RAGService.
Phase 3 will add sparse BM25 + RRF fusion + cross-encoder rerank here.
"""

from __future__ import annotations

from typing import Optional

from app.rag import KnowledgeChunk, get_rag_service


class HybridRetriever:
    """
    Retrieval orchestrator for the knowledge RAG read path.

    Phase 1: delegates to dense-only RAGService.search_dense.
    Phase 3: will call qdrant query_points with named-vector prefetch
             (dense + sparse RRF) followed by bge-reranker-v2-m3 rerank.
    """

    async def retrieve(
        self,
        query: str,
        top_k: int = 50,
        language: str | None = None,
    ) -> list[KnowledgeChunk]:
        svc = get_rag_service()
        query_vec = await svc._embed(query)
        return await svc._search_dense(query_vec, top_k=top_k, language=language)

    async def get_context_block(
        self,
        query: str,
        max_tokens: int = 1500,
        language: str | None = None,
    ) -> str:
        """Full pipeline: embed → retrieve → diversity filter → format."""
        return await get_rag_service().get_context_for_query(
            query=query,
            max_tokens=max_tokens,
        )
