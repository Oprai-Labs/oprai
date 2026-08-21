"""
WebSocket Chat Service

Real-time bidirectional chat streaming.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError


class _IncomingMessage(BaseModel):
    """Schema for incoming WebSocket messages."""
    type: str = "message"
    payload: dict = {}

logger = logging.getLogger(__name__)


@dataclass
class WebSocketMessage:
    """WebSocket message format"""
    type: str  # message, typing, action, error, etc.
    payload: Any
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_json(self) -> str:
        return json.dumps({
            "type": self.type,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
        })


class ConnectionManager:
    """Manages WebSocket connections"""

    def __init__(self):
        self._connections: dict[str, WebSocket] = {}
        self._user_sessions: dict[str, list[str]] = {}  # user_wallet -> [connection_ids]

    async def connect(self, websocket: WebSocket, user_wallet: str) -> str:
        """Accept a new WebSocket connection"""
        await websocket.accept()

        import uuid
        connection_id = str(uuid.uuid4())

        self._connections[connection_id] = websocket

        if user_wallet not in self._user_sessions:
            self._user_sessions[user_wallet] = []
        self._user_sessions[user_wallet].append(connection_id)

        logger.info(f"WebSocket connected: {connection_id} for user {user_wallet}")

        return connection_id

    async def disconnect(self, connection_id: str, user_wallet: str) -> None:
        """Disconnect a WebSocket"""
        self._connections.pop(connection_id, None)

        if user_wallet in self._user_sessions:
            if connection_id in self._user_sessions[user_wallet]:
                self._user_sessions[user_wallet].remove(connection_id)
            if not self._user_sessions[user_wallet]:
                del self._user_sessions[user_wallet]

        logger.info(f"WebSocket disconnected: {connection_id}")

    async def send(self, connection_id: str, message: WebSocketMessage) -> bool:
        """Send a message to a specific connection"""
        websocket = self._connections.get(connection_id)
        if not websocket:
            return False

        try:
            await websocket.send_text(message.to_json())
            return True
        except Exception as e:
            logger.error("Failed to send message", exc_info=True)
            return False

    async def broadcast_to_user(self, user_wallet: str, message: WebSocketMessage) -> int:
        """Broadcast a message to all connections for a user"""
        connection_ids = self._user_sessions.get(user_wallet, [])
        sent = 0

        for conn_id in connection_ids:
            if await self.send(conn_id, message):
                sent += 1

        return sent

    async def broadcast_all(self, message: WebSocketMessage) -> int:
        """Broadcast a message to all connections"""
        sent = 0

        for conn_id in list(self._connections.keys()):
            if await self.send(conn_id, message):
                sent += 1

        return sent

    def get_connection_count(self) -> int:
        """Get total number of connections"""
        return len(self._connections)


class WebSocketChatHandler:
    """Handles WebSocket chat sessions"""

    def __init__(
        self,
        connection_manager: ConnectionManager,
        agent_manager: Any = None,
        llm_service: Any = None,
    ):
        self.manager = connection_manager
        self.agent_manager = agent_manager
        self.llm_service = llm_service

    async def handle_session(
        self,
        websocket: WebSocket,
        user_wallet: str,
        character_id: str | None = None,
    ) -> None:
        """Handle a WebSocket chat session"""
        connection_id = await self.manager.connect(websocket, user_wallet)

        try:
            # Send welcome message
            await self.manager.send(connection_id, WebSocketMessage(
                type="connected",
                payload={"connection_id": connection_id, "character_id": character_id},
            ))

            while True:
                # Receive message
                data = await websocket.receive_text()

                try:
                    raw = json.loads(data)
                    message = _IncomingMessage.model_validate(raw)
                    await self._handle_message(
                        connection_id,
                        user_wallet,
                        message.model_dump(),
                        character_id,
                    )
                except json.JSONDecodeError:
                    await self.manager.send(connection_id, WebSocketMessage(
                        type="error",
                        payload={"message": "Invalid JSON"},
                    ))
                except ValidationError as exc:
                    await self.manager.send(connection_id, WebSocketMessage(
                        type="error",
                        payload={"message": "Invalid message format", "detail": exc.errors()},
                    ))

        except WebSocketDisconnect:
            pass
        finally:
            await self.manager.disconnect(connection_id, user_wallet)

    async def _handle_message(
        self,
        connection_id: str,
        user_wallet: str,
        message: dict,
        character_id: str | None,
    ) -> None:
        """Handle an incoming WebSocket message"""
        msg_type = message.get("type", "message")
        payload = message.get("payload", {})

        if msg_type == "message":
            await self._handle_chat_message(
                connection_id,
                user_wallet,
                payload.get("content", ""),
                character_id,
            )

        elif msg_type == "typing":
            # Broadcast typing indicator
            await self.manager.send(connection_id, WebSocketMessage(
                type="typing",
                payload={"is_typing": payload.get("is_typing", True)},
            ))

        elif msg_type == "action":
            await self._handle_action(
                connection_id,
                user_wallet,
                payload,
            )

    async def _handle_chat_message(
        self,
        connection_id: str,
        user_wallet: str,
        content: str,
        character_id: str | None,
    ) -> None:
        """Handle a chat message with streaming response"""
        from app.llm import get_llm_service
        from app.services.character import PromptBuilder, get_character_loader

        # Send typing indicator
        await self.manager.send(connection_id, WebSocketMessage(
            type="typing",
            payload={"is_typing": True},
        ))

        try:
            llm = get_llm_service()
            loader = get_character_loader()

            # Build messages
            messages = []

            if character_id:
                character = loader.get_character(character_id)
                if character:
                    builder = PromptBuilder(character)
                    system_prompt = builder.build_system_prompt()
                    messages.append({"role": "system", "content": system_prompt})

            messages.append({"role": "user", "content": content})

            # Stream response
            full_response = []

            async for chunk in llm.stream(messages):
                full_response.append(chunk)
                await self.manager.send(connection_id, WebSocketMessage(
                    type="chunk",
                    payload={"content": chunk},
                ))

            # Send complete message
            await self.manager.send(connection_id, WebSocketMessage(
                type="message",
                payload={
                    "role": "assistant",
                    "content": "".join(full_response),
                },
            ))

        except Exception as e:
            logger.error("Chat error", exc_info=True)
            await self.manager.send(connection_id, WebSocketMessage(
                type="error",
                payload={"message": str(e)},
            ))

        finally:
            # Stop typing indicator
            await self.manager.send(connection_id, WebSocketMessage(
                type="typing",
                payload={"is_typing": False},
            ))

    async def _handle_action(
        self,
        connection_id: str,
        user_wallet: str,
        payload: dict,
    ) -> None:
        """Handle an action request"""
        from app.plugins import get_plugin_manager

        action_name = payload.get("action")
        params = payload.get("params", {})

        try:
            manager = get_plugin_manager()
            context = {"user_wallet": user_wallet}

            result = await manager.execute_action(action_name, params, context)

            await self.manager.send(connection_id, WebSocketMessage(
                type="action_result",
                payload={
                    "action": action_name,
                    "success": result.success,
                    "data": result.data,
                    "error": result.error,
                },
            ))

        except Exception as e:
            logger.error("Action error", exc_info=True)
            await self.manager.send(connection_id, WebSocketMessage(
                type="error",
                payload={"message": str(e)},
            ))


# Global connection manager
_connection_manager: ConnectionManager | None = None


def get_connection_manager() -> ConnectionManager:
    """Get the global connection manager"""
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = ConnectionManager()
    return _connection_manager
