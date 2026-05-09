"""gRPC server for the memory service.

Exposes MemoryService, ConsentService, and SummaryService over gRPC
on a configurable port (default 50054).

Like the chat service, this module uses plain-class servicers that
can be wired up to generated ``_pb2_grpc`` stubs once proto compilation
is configured.
"""

import logging
from concurrent import futures

import grpc
from grpc import aio as grpc_aio

from app.config import settings
from app.db.connection import async_session_factory
from app.services.consent import get_consent, update_consent
from app.services.embeddings import EmbeddingService
from app.services.summary import SummaryService
from app.services.vector import VectorService

logger = logging.getLogger(__name__)


class MemoryServicer:
    """gRPC servicer for memory (vector) operations."""

    def __init__(self) -> None:
        self._vector = VectorService()
        self._embeddings: EmbeddingService | None = None
        try:
            self._embeddings = EmbeddingService()
        except RuntimeError:
            logger.warning("EmbeddingService not available (missing API key)")

    async def Store(self, request: dict, context: grpc.ServicerContext) -> dict:
        payload = request.get("payload", {})
        text = payload.get("summary", "")

        if not text:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "payload.summary required")
            return {}

        if not self._embeddings:
            context.abort(grpc.StatusCode.UNAVAILABLE, "Embedding service not configured")
            return {}

        vector = await self._embeddings.embed(text)
        point_id = await self._vector.store(payload, vector)
        return {"point_id": point_id}

    async def Search(self, request: dict, context: grpc.ServicerContext) -> dict:
        query = request.get("query", "")
        top_k = request.get("top_k", 5)
        threshold = request.get("threshold", 0.75)
        filters = request.get("filters")

        if not query:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "query required")
            return {}

        if not self._embeddings:
            context.abort(grpc.StatusCode.UNAVAILABLE, "Embedding service not configured")
            return {}

        query_vector = await self._embeddings.embed(query)
        results = await self._vector.search(
            query_vector, top_k=top_k, threshold=threshold, filters=filters,
        )
        return {"results": results}

    async def Delete(self, request: dict, context: grpc.ServicerContext) -> dict:
        point_id = request.get("point_id", "")
        if not point_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "point_id required")
            return {}

        deleted = await self._vector.delete(point_id)
        return {"ok": deleted}


class ConsentServicer:
    """gRPC servicer for consent operations."""

    async def GetConsent(self, request: dict, context: grpc.ServicerContext) -> dict:
        user_id = request.get("user_id", "")
        if not user_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id required")
            return {}

        async with async_session_factory() as db:
            consent = await get_consent(db, user_id)
            await db.commit()
        return {"user_id": user_id, "consent": consent}

    async def UpdateConsent(self, request: dict, context: grpc.ServicerContext) -> dict:
        user_id = request.get("user_id", "")
        fields = request.get("fields", {})

        if not user_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id required")
            return {}

        async with async_session_factory() as db:
            consent = await update_consent(db, user_id, fields)
            await db.commit()
        return {"user_id": user_id, "consent": consent}


class SummaryServicer:
    """gRPC servicer for summarization operations."""

    def __init__(self) -> None:
        self._svc = SummaryService()

    async def Summarize(self, request: dict, context: grpc.ServicerContext) -> dict:
        conversation_id = request.get("conversation_id", "")
        chunk = request.get("chunk", "")
        token_count = request.get("token_count", 0)

        if not chunk:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "chunk required")
            return {}

        summary = await self._svc.summarize(conversation_id, chunk, token_count)
        return {"summary": summary}


async def start_grpc_server() -> grpc_aio.Server:
    """Create and start the async gRPC server.

    Returns the server instance so it can be stopped during shutdown.
    """
    server = grpc_aio.server(
        futures.ThreadPoolExecutor(max_workers=10),
        options=[
            ("grpc.max_send_message_length", 50 * 1024 * 1024),
            ("grpc.max_receive_message_length", 50 * 1024 * 1024),
        ],
    )

    # NOTE: In production, register generated servicers from .proto files:
    #   memory_pb2_grpc.add_MemoryServiceServicer_to_server(MemoryServicer(), server)
    #   memory_pb2_grpc.add_ConsentServiceServicer_to_server(ConsentServicer(), server)
    #   memory_pb2_grpc.add_SummaryServiceServicer_to_server(SummaryServicer(), server)

    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    await server.start()
    logger.info("gRPC server started on %s", listen_addr)
    return server
