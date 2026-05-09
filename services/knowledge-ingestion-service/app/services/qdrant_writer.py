"""Qdrant batch writer — upserts named-vector points to oprai_blockchain_knowledge."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from app.config import settings
from app.services.sparse import SparseVector

logger = logging.getLogger(__name__)

COLLECTION_NAME = "oprai_blockchain_knowledge"
UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def make_point_id(doc_id: str, chunk_id: int) -> str:
    return str(uuid.uuid5(UUID_NAMESPACE, f"{doc_id}:{chunk_id}"))


@dataclass
class ChunkPayload:
    doc_id: str
    chunk_id: int
    content: str
    title: str
    section_path: str
    source_url: str
    source_type: str
    protocol: Optional[str]
    category: str
    language: str
    published_at: Optional[int]           # epoch ms
    content_hash: str
    tags: list[str] = field(default_factory=list)
    license: str = "proprietary-fair-use"
    embedding_model: str = "text-embedding-3-large"

    @property
    def token_count(self) -> int:
        return len(self.content.split())

    @property
    def fetched_at(self) -> int:
        return int(datetime.now(timezone.utc).timestamp() * 1000)


class QdrantWriter:
    def __init__(self) -> None:
        self._client: Optional[AsyncQdrantClient] = None

    def _get_client(self) -> AsyncQdrantClient:
        if self._client is None:
            self._client = AsyncQdrantClient(url=settings.QDRANT_URL)
        return self._client

    async def upsert_batch(
        self,
        payloads: list[ChunkPayload],
        dense_vecs: list[list[float]],
        sparse_vecs: list[SparseVector],
    ) -> int:
        """Upsert a batch of chunk points. Returns number of points upserted."""
        client = self._get_client()

        points: list[models.PointStruct] = []
        for payload, dense, sparse in zip(payloads, dense_vecs, sparse_vecs):
            point_id = make_point_id(payload.doc_id, payload.chunk_id)

            named_vectors: dict[str, Any] = {"dense": dense}
            if sparse.indices:
                named_vectors["sparse"] = models.SparseVector(
                    indices=sparse.indices,
                    values=sparse.values,
                )

            point_payload: dict[str, Any] = {
                "doc_id": payload.doc_id,
                "chunk_id": payload.chunk_id,
                "content": payload.content,
                "title": payload.title,
                "section_path": payload.section_path,
                "source_url": payload.source_url,
                "source_type": payload.source_type,
                "protocol": payload.protocol,
                "category": payload.category,
                "language": payload.language,
                "published_at": payload.published_at,
                "fetched_at": payload.fetched_at,
                "content_hash": payload.content_hash,
                "tags": payload.tags,
                "license": payload.license,
                "token_count": payload.token_count,
                "embedding_model": payload.embedding_model,
            }

            points.append(models.PointStruct(
                id=point_id,
                vector=named_vectors,
                payload=point_payload,
            ))

        if not points:
            return 0

        await client.upsert(
            collection_name=COLLECTION_NAME,
            points=points,
            wait=True,
        )
        logger.info("Upserted %d points to %s", len(points), COLLECTION_NAME)
        return len(points)

    async def delete_tail_chunks(self, doc_id: str, keep_up_to: int) -> None:
        """Delete chunks with chunk_id >= keep_up_to for a doc (shrinkage case)."""
        client = self._get_client()
        await client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="doc_id",
                            match=models.MatchValue(value=doc_id),
                        ),
                        models.FieldCondition(
                            key="chunk_id",
                            range=models.Range(gte=keep_up_to),
                        ),
                    ]
                )
            ),
        )

    async def count_by_content_hash(self, content_hash: str, doc_id: str, chunk_id: int) -> int:
        client = self._get_client()
        result = await client.count(
            collection_name=COLLECTION_NAME,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(key="content_hash", match=models.MatchValue(value=content_hash)),
                    models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id)),
                    models.FieldCondition(key="chunk_id", match=models.MatchValue(value=chunk_id)),
                ]
            ),
            exact=True,
        )
        return result.count
