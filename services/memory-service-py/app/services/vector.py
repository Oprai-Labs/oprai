"""Vector storage operations against Qdrant.

Handles point upsert, semantic search, and deletion using the
``qdrant-client`` SDK with async support.
"""

import logging
import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    HnswConfigDiff,
    MatchAny,
    PayloadSchemaType,
    PointStruct,
    QuantizationConfig,
    Range,
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType,
    VectorParams,
)

from app.config import settings

logger = logging.getLogger(__name__)


class VectorService:
    """Manages memory vectors in Qdrant."""

    def __init__(self) -> None:
        self._client = AsyncQdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY or None,
            timeout=10,
            check_compatibility=False,
        )
        self._collection = settings.COLLECTION_NAME
        self._dim = settings.EMBEDDING_DIM

    async def ensure_collection(self) -> None:
        """Create the collection and payload indexes if they do not already exist.

        Payload indexes on ``user_id`` and ``type`` turn filtered searches from
        full-collection scans into indexed lookups — critical at scale because
        every memory search is scoped to a single user.
        """
        try:
            await self._client.get_collection(self._collection)
            logger.info("Qdrant collection '%s' already exists", self._collection)
        except (UnexpectedResponse, Exception):
            logger.info("Creating Qdrant collection '%s'", self._collection)
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(
                    size=self._dim,
                    distance=Distance.COSINE,
                    # Store payload on disk to save RAM; frequently-accessed
                    # payload fields are covered by keyword indexes below.
                    on_disk=True,
                ),
                # HNSW: higher ef_construct = better recall at slightly slower
                # index-build time.  m=16 (default) is fine; ef_construct=200
                # improves recall for dense 1536-dim embeddings.
                hnsw_config=HnswConfigDiff(
                    ef_construct=200,
                    m=16,
                ),
                # INT8 scalar quantization: ~4× memory reduction vs float32
                # with <1% recall loss at this dimensionality.  Keeps the
                # raw vectors for re-scoring so accuracy stays high.
                quantization_config=QuantizationConfig(
                    scalar=ScalarQuantization(
                        scalar=ScalarQuantizationConfig(
                            type=ScalarType.INT8,
                            always_ram=True,
                        )
                    )
                ),
            )

        # Payload indexes are idempotent — safe to re-run on every boot.
        # Without these, filtered searches do a full collection scan.
        await self._ensure_payload_indexes()

    async def _ensure_payload_indexes(self) -> None:
        """Create payload indexes for frequently-filtered fields."""
        for field_name in ("user_id", "type"):
            try:
                await self._client.create_payload_index(
                    collection_name=self._collection,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
                logger.debug("Payload index ensured: %s.%s", self._collection, field_name)
            except Exception:
                # Index already exists or transient error — not fatal.
                logger.debug(
                    "Payload index skipped (already exists or error): %s.%s",
                    self._collection,
                    field_name,
                )

    async def store(
        self,
        payload: dict[str, Any],
        vector: list[float],
        point_id: str | None = None,
    ) -> str:
        """Upsert a single point into the collection.

        Parameters
        ----------
        payload:
            Arbitrary JSON payload stored alongside the vector.
        vector:
            The embedding vector (must match EMBEDDING_DIM).
        point_id:
            Optional explicit point ID.  If not given, a UUID is generated.

        Returns
        -------
        str
            The point ID that was upserted.
        """
        pid = point_id or str(uuid.uuid4())
        await self._client.upsert(
            collection_name=self._collection,
            points=[
                PointStruct(
                    id=pid,
                    vector=vector,
                    payload=payload,
                )
            ],
        )
        logger.debug("Upserted point %s", pid)
        return pid

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        threshold: float = 0.75,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search against the collection.

        Parameters
        ----------
        query_vector:
            The embedding of the search query.
        top_k:
            Maximum number of results to return.
        threshold:
            Minimum cosine similarity score.
        filters:
            Optional filter dict with keys ``user_id`` and/or ``types``.

        Returns
        -------
        list[dict]
            Each dict has ``id``, ``score``, and ``payload`` keys.
        """
        qdrant_filter = self._build_filter(filters)

        try:
            results = await self._client.query_points(
                collection_name=self._collection,
                query=query_vector,
                query_filter=qdrant_filter,
                limit=top_k,
                score_threshold=threshold,
                with_payload=True,
            )

            return [
                {
                    "id": str(point.id),
                    "score": point.score,
                    "payload": point.payload or {},
                }
                for point in results.points
            ]
        except Exception:
            logger.exception("Qdrant search failed")
            return []

    async def get_by_id(self, point_id: str) -> dict[str, Any] | None:
        """Fetch a single point's payload by ID. Returns None if not found."""
        try:
            results = await self._client.retrieve(
                collection_name=self._collection,
                ids=[point_id],
                with_payload=True,
                with_vectors=False,
            )
            if not results:
                return None
            payload = results[0].payload or {}
            return {"id": str(results[0].id), "payload": payload}
        except Exception:
            logger.exception("Qdrant retrieve failed for point %s", point_id)
            return None

    async def delete(self, point_id: str) -> bool:
        """Delete a single point by ID.

        Returns True if the operation succeeded (Qdrant does not error
        on missing IDs).
        """
        try:
            await self._client.delete(
                collection_name=self._collection,
                points_selector=[point_id],
            )
            logger.debug("Deleted point %s", point_id)
            return True
        except Exception:
            logger.exception("Qdrant delete failed for point %s", point_id)
            return False

    async def delete_by_wallet(self, wallet: str) -> int:
        """Delete every memory point owned by `wallet`.

        Returns the count of points removed. Best-effort: Qdrant does not
        return per-row deletion counts on filter-based delete, so we count
        via a scroll first and then issue the bulk delete.
        """
        try:
            point_ids: list[str] = []
            offset: Any = None
            while True:
                points, offset = await self._client.scroll(
                    collection_name=self._collection,
                    scroll_filter=Filter(must=[
                        FieldCondition(key="user_id", match=MatchAny(any=[wallet])),
                    ]),
                    with_payload=False,
                    with_vectors=False,
                    limit=512,
                    offset=offset,
                )
                point_ids.extend(str(p.id) for p in points)
                if offset is None:
                    break
            if not point_ids:
                return 0
            await self._client.delete(
                collection_name=self._collection,
                points_selector=point_ids,
            )
            logger.info("Deleted %d memory points for wallet %s", len(point_ids), wallet[:10])
            return len(point_ids)
        except Exception:
            logger.exception("Qdrant bulk-delete failed for wallet %s", wallet[:10])
            return 0

    def _build_filter(self, filters: dict[str, Any] | None) -> Filter | None:
        """Build a Qdrant filter from the request-level filter dict."""
        if not filters:
            return None

        conditions: list[FieldCondition] = []

        user_id = filters.get("user_id")
        if user_id:
            conditions.append(
                FieldCondition(key="user_id", match=MatchAny(any=[user_id]))
            )

        types = filters.get("types")
        if types and isinstance(types, list):
            conditions.append(
                FieldCondition(key="type", match=MatchAny(any=types))
            )

        if not conditions:
            return None

        return Filter(must=conditions)

    async def close(self) -> None:
        """Close the underlying Qdrant client."""
        await self._client.close()
