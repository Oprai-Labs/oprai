"""
RAG (Retrieval Augmented Generation) Integration

Connects document ingestion to Qdrant vector store for knowledge retrieval.
"""

from __future__ import annotations

import asyncio
import logging
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.config import settings
from app.llm import get_llm_service

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeChunk:
    """A chunk of knowledge for indexing"""
    id: str
    content: str
    source: str
    source_type: str  # pdf, url, video, etc.
    chunk_index: int
    embedding: Optional[list[float]] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    """Result from knowledge search"""
    chunk: KnowledgeChunk
    score: float
    highlights: list[str] = field(default_factory=list)


class RAGService:
    """
    Retrieval Augmented Generation service.

    Manages:
    - Document chunking and embedding
    - Vector storage in Qdrant
    - Semantic search
    - Context building for LLM
    """

    COLLECTION_NAME = "oprai_knowledge"
    EMBEDDING_DIMENSION = 1536  # OpenAI text-embedding-3-large

    def __init__(
        self,
        qdrant_url: Optional[str] = None,
        embedding_model: str = "text-embedding-3-large",
    ):
        self.qdrant_url = qdrant_url or settings.QDRANT_URL
        self.embedding_model = embedding_model
        self._client: Optional[AsyncQdrantClient] = None
        self._llm = None

    async def _get_client(self) -> AsyncQdrantClient:
        """Get Qdrant client"""
        if self._client is None:
            self._client = AsyncQdrantClient(url=self.qdrant_url)
            await self._ensure_collection()
        return self._client

    async def _get_llm(self):
        """Get LLM service"""
        if self._llm is None:
            self._llm = get_llm_service()
        return self._llm

    async def _ensure_collection(self) -> None:
        """Ensure the knowledge collection exists"""
        client = await self._get_client()

        try:
            await client.get_collection(self.COLLECTION_NAME)
        except UnexpectedResponse:
            # Collection doesn't exist, create it
            await client.create_collection(
                collection_name=self.COLLECTION_NAME,
                vectors_config=models.VectorParams(
                    size=self.EMBEDDING_DIMENSION,
                    distance=models.Distance.COSINE,
                ),
            )
            logger.info(f"Created collection: {self.COLLECTION_NAME}")

    async def ingest_document(
        self,
        content: str,
        source: str,
        source_type: str,
        owner_wallet: Optional[str] = None,
        chunk_size: int = 500,
        chunk_overlap: int = 100,
        metadata: Optional[dict[str, Any]] = None,
    ) -> list[KnowledgeChunk]:
        """
        Ingest a document into the knowledge base.

        Args:
            content: Document text content
            source: Source identifier (file path, URL, etc.)
            source_type: Type of source (pdf, url, video, etc.)
            owner_wallet: Owner's wallet address
            chunk_size: Size of chunks in characters
            chunk_overlap: Overlap between chunks
            metadata: Additional metadata

        Returns:
            List of created chunks
        """
        client = await self._get_client()
        llm = await self._get_llm()

        # Split into chunks
        chunks = self._chunk_text(content, chunk_size, chunk_overlap)

        knowledge_chunks = []
        points = []

        for i, chunk_text in enumerate(chunks):
            # Generate ID
            chunk_id = hashlib.sha256(
                f"{source}:{i}:{chunk_text[:50]}".encode()
            ).hexdigest()

            # Generate embedding
            embedding = await llm.embed(chunk_text)

            # Create chunk
            chunk = KnowledgeChunk(
                id=chunk_id,
                content=chunk_text,
                source=source,
                source_type=source_type,
                chunk_index=i,
                embedding=embedding,
                metadata={
                    **(metadata or {}),
                    "owner_wallet": owner_wallet,
                    "ingested_at": datetime.utcnow().isoformat(),
                },
            )
            knowledge_chunks.append(chunk)

            # Create point for Qdrant
            points.append(models.PointStruct(
                id=chunk_id,
                vector=embedding,
                payload={
                    "content": chunk_text,
                    "source": source,
                    "source_type": source_type,
                    "chunk_index": i,
                    "owner_wallet": owner_wallet,
                    "metadata": metadata or {},
                },
            ))

        # Batch upload to Qdrant
        if points:
            await client.upsert(
                collection_name=self.COLLECTION_NAME,
                points=points,
            )

        logger.info(f"Ingested {len(points)} chunks from {source}")
        return knowledge_chunks

    async def search(
        self,
        query: str,
        limit: int = 5,
        owner_wallet: Optional[str] = None,
        source_types: Optional[list[str]] = None,
        min_score: float = 0.5,
    ) -> list[SearchResult]:
        """
        Search the knowledge base.

        Args:
            query: Search query
            limit: Maximum results
            owner_wallet: Filter by owner
            source_types: Filter by source types
            min_score: Minimum similarity score

        Returns:
            List of search results
        """
        client = await self._get_client()
        llm = await self._get_llm()

        # Generate query embedding
        query_embedding = await llm.embed(query)

        # Build filter
        filter_conditions = []

        if owner_wallet:
            filter_conditions.append(
                models.FieldCondition(
                    key="owner_wallet",
                    match=models.MatchValue(value=owner_wallet),
                )
            )

        if source_types:
            filter_conditions.append(
                models.FieldCondition(
                    key="source_type",
                    match=models.MatchAny(any=source_types),
                )
            )

        query_filter = None
        if filter_conditions:
            query_filter = models.Filter(must=filter_conditions)

        # Search
        results = await client.search(
            collection_name=self.COLLECTION_NAME,
            query_vector=query_embedding,
            limit=limit,
            query_filter=query_filter,
            score_threshold=min_score,
        )

        # Convert to SearchResult
        search_results = []
        for result in results:
            chunk = KnowledgeChunk(
                id=str(result.id),
                content=result.payload.get("content", ""),
                source=result.payload.get("source", ""),
                source_type=result.payload.get("source_type", ""),
                chunk_index=result.payload.get("chunk_index", 0),
            )
            search_results.append(SearchResult(
                chunk=chunk,
                score=result.score,
            ))

        return search_results

    async def get_context_for_query(
        self,
        query: str,
        max_tokens: int = 2000,
        owner_wallet: Optional[str] = None,
    ) -> str:
        """
        Get relevant context for a query to use in LLM prompt.

        Args:
            query: User query
            max_tokens: Approximate max tokens for context
            owner_wallet: Filter by owner

        Returns:
            Context string for LLM
        """
        results = await self.search(
            query,
            limit=10,
            owner_wallet=owner_wallet,
        )

        if not results:
            return ""

        context_parts = []
        current_length = 0

        for result in results:
            content = result.chunk.content
            source = result.chunk.source

            part = f"[Source: {source}]\n{content}\n"
            part_length = len(part.split())  # Rough token estimate

            if current_length + part_length > max_tokens:
                break

            context_parts.append(part)
            current_length += part_length

        return "\n---\n".join(context_parts)

    async def delete_document(
        self,
        source: str,
        owner_wallet: Optional[str] = None,
    ) -> int:
        """
        Delete all chunks from a document.

        Args:
            source: Source identifier
            owner_wallet: Owner filter

        Returns:
            Number of deleted chunks
        """
        client = await self._get_client()

        # Build filter
        conditions = [
            models.FieldCondition(
                key="source",
                match=models.MatchValue(value=source),
            )
        ]

        if owner_wallet:
            conditions.append(
                models.FieldCondition(
                    key="owner_wallet",
                    match=models.MatchValue(value=owner_wallet),
                )
            )

        # Delete by filter
        await client.delete(
            collection_name=self.COLLECTION_NAME,
            points_selector=models.FilterSelector(
                filter=models.Filter(must=conditions),
            ),
        )

        logger.info(f"Deleted chunks from: {source}")
        return 0  # Qdrant doesn't return count

    async def clear_all(self, owner_wallet: Optional[str] = None) -> None:
        """Clear all knowledge (optionally filtered by owner)"""
        client = await self._get_client()

        if owner_wallet:
            await client.delete(
                collection_name=self.COLLECTION_NAME,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="owner_wallet",
                                match=models.MatchValue(value=owner_wallet),
                            )
                        ]
                    )
                ),
            )
        else:
            # Delete entire collection and recreate
            await client.delete_collection(self.COLLECTION_NAME)
            await self._ensure_collection()

        logger.info("Cleared knowledge base")

    async def get_stats(self) -> dict[str, Any]:
        """Get knowledge base statistics"""
        client = await self._get_client()

        info = await client.get_collection(self.COLLECTION_NAME)

        return {
            "total_chunks": info.points_count,
            "collection_name": self.COLLECTION_NAME,
            "vector_size": info.config.params.vectors.size,
            "status": info.status.value,
        }

    def _chunk_text(
        self,
        text: str,
        chunk_size: int = 500,
        chunk_overlap: int = 100,
    ) -> list[str]:
        """Split text into overlapping chunks"""
        words = text.split()
        chunks = []

        start = 0
        while start < len(words):
            end = start + chunk_size
            chunk_words = words[start:end]
            chunks.append(" ".join(chunk_words))

            start += chunk_size - chunk_overlap
            if start >= len(words):
                break

        return chunks


# Global RAG service
_rag_service: Optional[RAGService] = None


def get_rag_service() -> RAGService:
    """Get the global RAG service"""
    global _rag_service
    if _rag_service is None:
        _rag_service = RAGService()
    return _rag_service
