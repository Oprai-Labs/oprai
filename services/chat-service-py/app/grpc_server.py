"""gRPC server for the chat service.

Uses generated stubs from proto/chat/{session,message}.proto compiled by
grpcio-tools into proto_gen/.  Run ``make proto`` (or the build script) to
regenerate if the .proto files change.
"""

import asyncio
import logging
import sys
import os
from concurrent import futures
from pathlib import Path

import grpc
from grpc import aio as grpc_aio

from app.config import settings
from app.db.connection import async_session_factory
from app.services import session as session_svc
from app.services import message as message_svc

logger = logging.getLogger(__name__)

# Add proto_gen to sys.path so the generated stubs can resolve their own imports
_PROTO_GEN = Path(__file__).parent.parent / "proto_gen"
if str(_PROTO_GEN) not in sys.path:
    sys.path.insert(0, str(_PROTO_GEN))

try:
    from proto.chat import session_pb2, session_pb2_grpc  # type: ignore
    from proto.chat import message_pb2, message_pb2_grpc  # type: ignore
    _STUBS_AVAILABLE = True
except ImportError as _import_err:
    logger.warning(
        "gRPC proto stubs not found (%s). Run `make proto` to generate them. "
        "gRPC servicers will NOT be registered until stubs exist.",
        _import_err,
    )
    _STUBS_AVAILABLE = False


# ─── Session Servicer ─────────────────────────────────────────────────────────

class _ChatSessionServicer(session_pb2_grpc.ChatSessionServiceServicer if _STUBS_AVAILABLE else object):  # type: ignore[misc]
    """Implements ChatSessionService gRPC endpoints."""

    async def CreateSession(self, request, context):
        wallet = request.wallet
        if not wallet:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "wallet is required")
        async with async_session_factory() as db:
            sess = await session_svc.create_session(db, wallet, wallet, request.title or "New chat")
            await db.commit()
        return _session_to_proto(sess)

    async def GetSession(self, request, context):
        async with async_session_factory() as db:
            sess = await session_svc.get_session(db, request.wallet, request.session_id)
            await db.commit()
        if sess is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Session not found")
        return _session_to_proto(sess)

    async def ListSessions(self, request, context):
        async with async_session_factory() as db:
            sessions = await session_svc.list_sessions(db, request.wallet)
            await db.commit()
        return session_pb2.ListSessionsResponse(
            sessions=[_session_to_proto(s) for s in sessions],
        )

    async def UpdateSessionTitle(self, request, context):
        async with async_session_factory() as db:
            sess = await session_svc.update_title(db, request.wallet, request.session_id, request.title)
            await db.commit()
        if sess is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Session not found")
        return _session_to_proto(sess)

    async def DeleteSession(self, request, context):
        async with async_session_factory() as db:
            deleted = await session_svc.delete_session(db, request.wallet, request.session_id)
            await db.commit()
        if not deleted:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Session not found")
        return session_pb2.DeleteSessionResponse(success=True)


# ─── Message Servicer ─────────────────────────────────────────────────────────

class _ChatMessageServicer(message_pb2_grpc.ChatMessageServiceServicer if _STUBS_AVAILABLE else object):  # type: ignore[misc]
    """Implements ChatMessageService gRPC endpoints."""

    async def GetMessages(self, request, context):
        pagination = request.pagination
        limit = pagination.limit if pagination.limit > 0 else 50
        offset = max(0, (pagination.page - 1) * limit) if pagination.page > 1 else 0

        async with async_session_factory() as db:
            messages = await message_svc.get_messages(
                db, request.wallet, request.session_id,
                limit=min(limit, 200), offset=offset,
            )
            await db.commit()
        return message_pb2.GetMessagesResponse(
            messages=[_message_to_proto(m) for m in messages],
        )

    async def SendMessage(self, request, context):
        """Streaming RPC: streams token deltas then the completed message."""
        import json
        from datetime import datetime, timezone
        from app.services import session as session_svc

        session_id = request.session_id
        wallet = request.wallet
        content = request.content

        if not all([session_id, wallet, content]):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "session_id, wallet, and content are required")

        async with async_session_factory() as db:
            # Determine whether this is the first message (needed for title generation)
            session = await session_svc.get_session(db, wallet, session_id)
            is_first_message = session is None or session.get("messageCount", 0) == 0

            async for sse_line in message_svc.stream_chat_response(
                db, session_id, wallet, content,
                is_first_message=is_first_message,
            ):
                if not sse_line.startswith("data: "):
                    continue
                raw = sse_line[6:].strip()
                if raw == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                except Exception:
                    continue

                if "delta" in chunk:
                    yield message_pb2.SendMessageResponse(
                        token=message_pb2.StreamToken(delta=chunk["delta"])
                    )
                elif "error" in chunk:
                    await context.abort(grpc.StatusCode.INTERNAL, chunk["error"])
                    return

            await db.commit()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _session_to_proto(sess: dict):
    from google.protobuf.timestamp_pb2 import Timestamp
    from datetime import datetime, timezone

    def _ts(iso: str | None):
        ts = Timestamp()
        if iso:
            try:
                dt = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
                ts.FromDatetime(dt)
            except Exception:
                pass
        return ts

    return session_pb2.SessionMeta(
        id=sess.get("id", ""),
        wallet=sess.get("wallet_address", ""),
        title=sess.get("title", ""),
        created_at=_ts(sess.get("created_at")),
        updated_at=_ts(sess.get("updated_at")),
    )


def _message_to_proto(msg: dict):
    from google.protobuf.timestamp_pb2 import Timestamp
    from datetime import datetime, timezone

    role_map = {"user": 1, "assistant": 2, "system": 3}

    ts = Timestamp()
    iso = msg.get("timestamp")
    if iso:
        try:
            dt = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
            ts.FromDatetime(dt)
        except Exception:
            pass

    return message_pb2.ChatMessage(
        id=msg.get("id", ""),
        speaker=role_map.get(msg.get("speaker", "user"), 1),
        content=msg.get("content", ""),
        streaming=False,
        timestamp=ts,
    )


# ─── Server start ─────────────────────────────────────────────────────────────

async def start_grpc_server() -> grpc_aio.Server:
    """Create, configure, and start the async gRPC server.

    Returns the server instance so it can be gracefully stopped during shutdown.
    """
    server = grpc_aio.server(
        futures.ThreadPoolExecutor(max_workers=10),
        options=[
            ("grpc.max_send_message_length", 50 * 1024 * 1024),
            ("grpc.max_receive_message_length", 50 * 1024 * 1024),
            ("grpc.keepalive_time_ms", 30_000),
            ("grpc.keepalive_timeout_ms", 10_000),
        ],
    )

    if _STUBS_AVAILABLE:
        session_pb2_grpc.add_ChatSessionServiceServicer_to_server(_ChatSessionServicer(), server)
        message_pb2_grpc.add_ChatMessageServiceServicer_to_server(_ChatMessageServicer(), server)
        logger.info("Registered ChatSessionService + ChatMessageService gRPC servicers")
    else:
        logger.error(
            "gRPC servicers NOT registered — proto stubs missing. "
            "Run `make proto` then restart the service."
        )

    # Bind to loopback only. The gateway (the only gRPC client) is on the
    # same host and connects via `localhost:50052`. Using `[::]` (IPv6
    # wildcard) means any outbound connection from this machine that
    # happens to grab port 50052 as its ephemeral source steals our slot
    # — mDNSResponder picking 50052 for an outbound DoH connection to
    # 8.8.8.8:443 has been observed reliably on macOS, where the default
    # ephemeral range (49152+) overlaps our service ports. `127.0.0.1`
    # only accepts loopback traffic, so external outbound connections
    # (which use the LAN IP as source) can't collide with it.
    grpc_host = os.environ.get("GRPC_HOST", "127.0.0.1")
    listen_addr = f"{grpc_host}:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    await server.start()
    logger.info("gRPC server listening on %s", listen_addr)
    return server
