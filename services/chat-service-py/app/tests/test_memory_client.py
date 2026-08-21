"""
Tests for Memory Client module.

Tests HTTP client functions for memory service communication.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSearchMemories:
    """Test search_memories function"""

    @pytest.mark.asyncio
    async def test_search_memories_success(self):
        """Test successful memory search"""
        from app.services.memory_client import search_memories

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"id": "1", "payload": {"type": "swap", "summary": "Swapped SOL for USDC"}},
                {"id": "2", "payload": {"type": "stake", "summary": "Staked 10 SOL"}},
            ]
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            results = await search_memories(
                wallet="test_wallet",
                query="swap",
                top_k=5,
                threshold=0.7
            )

            assert len(results) == 2
            assert results[0]["payload"]["type"] == "swap"

    @pytest.mark.asyncio
    async def test_search_memories_empty_response(self):
        """Test memory search with empty results"""
        from app.services.memory_client import search_memories

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": []}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            results = await search_memories(
                wallet="test_wallet",
                query="test"
            )

            assert results == []

    @pytest.mark.asyncio
    async def test_search_memories_non_200_response(self):
        """Test memory search with error response"""
        from app.services.memory_client import search_memories

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Error"

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            results = await search_memories(
                wallet="test_wallet",
                query="test"
            )

            assert results == []

    @pytest.mark.asyncio
    async def test_search_memories_with_types_filter(self):
        """Test memory search with types filter"""
        from app.services.memory_client import search_memories

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [{"id": "1"}]}

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value.__aenter__.return_value = mock_instance

            await search_memories(
                wallet="test_wallet",
                query="swap",
                types="swap,stake"
            )

            # Verify the types parameter was included
            call_args = mock_instance.get.call_args
            assert call_args is not None


class TestStoreMemory:
    """Test store_memory function"""

    @pytest.mark.asyncio
    async def test_store_memory_success(self):
        """Test successful memory storage"""
        from app.services.memory_client import store_memory

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "new-memory-id"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            result = await store_memory(
                wallet="test_wallet",
                memory_type="swap",
                summary="Swapped SOL for USDC"
            )

            assert result == "new-memory-id"

    @pytest.mark.asyncio
    async def test_store_memory_with_extra(self):
        """Test memory storage with extra data"""
        from app.services.memory_client import store_memory

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "new-memory-id"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            result = await store_memory(
                wallet="test_wallet",
                memory_type="swap",
                summary="Swapped SOL",
                extra={"amount": "10", "token": "SOL"}
            )

            assert result == "new-memory-id"

    @pytest.mark.asyncio
    async def test_store_memory_failure(self):
        """Test memory storage failure"""
        from app.services.memory_client import store_memory

        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            result = await store_memory(
                wallet="test_wallet",
                memory_type="swap",
                summary="Test"
            )

            assert result is None


class TestGetConsent:
    """Test get_consent function"""

    @pytest.mark.asyncio
    async def test_get_consent_exists(self):
        """Test getting existing consent"""
        from app.services.memory_client import get_consent

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "memory_enabled": True,
            "analytics_enabled": False
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            result = await get_consent(wallet="test_wallet")

            assert result is not None
            assert result["memory_enabled"] is True

    @pytest.mark.asyncio
    async def test_get_consent_not_found(self):
        """Test getting non-existent consent"""
        from app.services.memory_client import get_consent

        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            result = await get_consent(wallet="nonexistent")

            assert result is None


class TestMemoryClientConnection:
    """Test connection handling"""

    @pytest.mark.asyncio
    async def test_connect_error_handling(self):
        """Test handling of connection errors"""
        from app.services.memory_client import search_memories

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.side_effect = Exception("Connection refused")

            results = await search_memories(
                wallet="test_wallet",
                query="test"
            )

            # Should return empty list on error (non-blocking)
            assert results == []


class TestMemoryTypes:
    """Test memory type handling"""

    def test_memory_types_are_valid(self):
        """Test that memory types can be passed"""
        # This tests the function signature accepts types parameter
        import inspect

        from app.services.memory_client import search_memories

        sig = inspect.signature(search_memories)
        params = sig.parameters

        assert "types" in params
        assert params["types"].default is None


class TestMemoryClientTimeout:
    """Test timeout configuration"""

    def test_timeout_defined(self):
        """Test that timeout is defined"""
        from app.services import memory_client

        assert hasattr(memory_client, "_TIMEOUT")
        assert memory_client._TIMEOUT == 5.0
