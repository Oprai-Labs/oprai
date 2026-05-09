"""
Tests for WebSocket Chat Service module.

Tests WebSocket connection management and message handling.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
import json


class TestWebSocketMessage:
    """Test WebSocketMessage dataclass"""

    def test_message_creation(self):
        """Test creating a WebSocketMessage"""
        from app.websocket import WebSocketMessage

        msg = WebSocketMessage(
            type="message",
            payload={"content": "Hello"}
        )

        assert msg.type == "message"
        assert msg.payload["content"] == "Hello"

    def test_message_defaults(self):
        """Test message default values"""
        from app.websocket import WebSocketMessage

        msg = WebSocketMessage(type="test", payload={})

        assert msg.timestamp is not None
        assert isinstance(msg.timestamp, datetime)

    def test_to_json(self):
        """Test converting message to JSON"""
        from app.websocket import WebSocketMessage

        msg = WebSocketMessage(
            type="message",
            payload={"content": "Hello"}
        )

        json_str = msg.to_json()
        data = json.loads(json_str)

        assert data["type"] == "message"
        assert data["payload"]["content"] == "Hello"
        assert "timestamp" in data


class TestConnectionManagerInit:
    """Test ConnectionManager initialization"""

    def test_init(self):
        """Test manager initialization"""
        from app.websocket import ConnectionManager

        manager = ConnectionManager()

        assert manager._connections == {}
        assert manager._user_sessions == {}


class TestConnectionManagerConnect:
    """Test connection management"""

    @pytest.mark.asyncio
    async def test_connect(self):
        """Test connecting a new WebSocket"""
        from app.websocket import ConnectionManager

        mock_websocket = MagicMock()
        mock_websocket.accept = AsyncMock()

        manager = ConnectionManager()
        connection_id = await manager.connect(mock_websocket, "wallet123")

        assert connection_id is not None
        assert len(connection_id) > 0
        assert "wallet123" in manager._user_sessions
        mock_websocket.accept.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_multiple_wallets(self):
        """Test connecting multiple wallets"""
        from app.websocket import ConnectionManager

        mock_ws1 = MagicMock()
        mock_ws1.accept = AsyncMock()
        mock_ws2 = MagicMock()
        mock_ws2.accept = AsyncMock()

        manager = ConnectionManager()
        conn1 = await manager.connect(mock_ws1, "wallet1")
        conn2 = await manager.connect(mock_ws2, "wallet2")

        assert conn1 != conn2
        assert len(manager._connections) == 2


class TestConnectionManagerDisconnect:
    """Test disconnection"""

    @pytest.mark.asyncio
    async def test_disconnect(self):
        """Test disconnecting"""
        from app.websocket import ConnectionManager

        mock_websocket = MagicMock()
        mock_websocket.accept = AsyncMock()

        manager = ConnectionManager()
        connection_id = await manager.connect(mock_websocket, "wallet123")

        await manager.disconnect(connection_id, "wallet123")

        assert connection_id not in manager._connections
        assert "wallet123" not in manager._user_sessions

    @pytest.mark.asyncio
    async def test_disconnect_partial(self):
        """Test disconnecting one of multiple connections"""
        from app.websocket import ConnectionManager

        mock_ws1 = MagicMock()
        mock_ws1.accept = AsyncMock()
        mock_ws2 = MagicMock()
        mock_ws2.accept = AsyncMock()

        manager = ConnectionManager()
        conn1 = await manager.connect(mock_ws1, "wallet1")
        conn2 = await manager.connect(mock_ws2, "wallet1")

        await manager.disconnect(conn1, "wallet1")

        assert conn1 not in manager._connections
        assert conn2 in manager._connections
        assert conn1 not in manager._user_sessions["wallet1"]
        assert conn2 in manager._user_sessions["wallet1"]


class TestConnectionManagerSend:
    """Test message sending"""

    @pytest.mark.asyncio
    async def test_send_success(self):
        """Test sending message successfully"""
        from app.websocket import ConnectionManager, WebSocketMessage

        mock_websocket = MagicMock()
        mock_websocket.accept = AsyncMock()
        mock_websocket.send_text = AsyncMock()

        manager = ConnectionManager()
        connection_id = await manager.connect(mock_websocket, "wallet123")

        msg = WebSocketMessage(type="message", payload={"text": "Hello"})
        result = await manager.send(connection_id, msg)

        assert result is True
        mock_websocket.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_invalid_connection(self):
        """Test sending to invalid connection"""
        from app.websocket import ConnectionManager, WebSocketMessage

        manager = ConnectionManager()

        msg = WebSocketMessage(type="message", payload={})
        result = await manager.send("invalid-id", msg)

        assert result is False


class TestConnectionManagerBroadcast:
    """Test broadcasting"""

    @pytest.mark.asyncio
    async def test_broadcast_to_user(self):
        """Test broadcasting to user"""
        from app.websocket import ConnectionManager, WebSocketMessage

        mock_ws1 = MagicMock()
        mock_ws1.accept = AsyncMock()
        mock_ws1.send_text = AsyncMock()
        mock_ws2 = MagicMock()
        mock_ws2.accept = AsyncMock()
        mock_ws2.send_text = AsyncMock()

        manager = ConnectionManager()
        await manager.connect(mock_ws1, "wallet1")
        await manager.connect(mock_ws2, "wallet1")

        msg = WebSocketMessage(type="broadcast", payload={"text": "Hello"})
        sent = await manager.broadcast_to_user("wallet1", msg)

        assert sent == 2

    @pytest.mark.asyncio
    async def test_broadcast_to_user_not_found(self):
        """Test broadcasting to non-existent user"""
        from app.websocket import ConnectionManager, WebSocketMessage

        manager = ConnectionManager()

        msg = WebSocketMessage(type="broadcast", payload={})
        sent = await manager.broadcast_to_user("nonexistent", msg)

        assert sent == 0

    @pytest.mark.asyncio
    async def test_broadcast_all(self):
        """Test broadcasting to all"""
        from app.websocket import ConnectionManager, WebSocketMessage

        mock_ws1 = MagicMock()
        mock_ws1.accept = AsyncMock()
        mock_ws1.send_text = AsyncMock()
        mock_ws2 = MagicMock()
        mock_ws2.accept = AsyncMock()
        mock_ws2.send_text = AsyncMock()

        manager = ConnectionManager()
        await manager.connect(mock_ws1, "wallet1")
        await manager.connect(mock_ws2, "wallet2")

        msg = WebSocketMessage(type="broadcast", payload={})
        sent = await manager.broadcast_all(msg)

        assert sent == 2


class TestConnectionManagerStats:
    """Test connection statistics"""

    def test_get_connection_count(self):
        """Test getting connection count"""
        from app.websocket import ConnectionManager

        manager = ConnectionManager()

        assert manager.get_connection_count() == 0

    def test_get_connection_count_with_connections(self):
        """Test getting count with active connections"""
        from app.websocket import ConnectionManager

        mock_ws = MagicMock()
        mock_ws.accept = AsyncMock()

        manager = ConnectionManager()

        # Use sync approach to add connections without async
        manager._connections["conn1"] = MagicMock()
        manager._connections["conn2"] = MagicMock()

        assert manager.get_connection_count() == 2


class TestWebSocketChatHandler:
    """Test WebSocketChatHandler"""

    def test_init(self):
        """Test handler initialization"""
        from app.websocket import WebSocketChatHandler, ConnectionManager

        manager = ConnectionManager()
        handler = WebSocketChatHandler(manager)

        assert handler.manager is manager
        assert handler.agent_manager is None
        assert handler.llm_service is None

    def test_init_with_services(self):
        """Test handler initialization with services"""
        from app.websocket import WebSocketChatHandler, ConnectionManager

        manager = ConnectionManager()
        mock_agent = MagicMock()
        mock_llm = MagicMock()

        handler = WebSocketChatHandler(
            connection_manager=manager,
            agent_manager=mock_agent,
            llm_service=mock_llm
        )

        assert handler.agent_manager is mock_agent
        assert handler.llm_service is mock_llm


class TestGlobalConnectionManager:
    """Test global connection manager singleton"""

    def test_get_connection_manager(self):
        """Test getting global connection manager"""
        from app.websocket import get_connection_manager, ConnectionManager

        # Reset global
        import app.websocket as ws_module
        ws_module._connection_manager = None

        manager = get_connection_manager()

        assert manager is not None
        assert isinstance(manager, ConnectionManager)

    def test_singleton(self):
        """Test singleton behavior"""
        from app.websocket import get_connection_manager

        # Reset global
        import app.websocket as ws_module
        ws_module._connection_manager = None

        manager1 = get_connection_manager()
        manager2 = get_connection_manager()

        assert manager1 is manager2
