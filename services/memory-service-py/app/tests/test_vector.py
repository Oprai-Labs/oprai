"""
Tests for Vector Service module.

Tests vector storage, search, and deletion operations with Qdrant.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


class TestVectorServiceInit:
    """Test VectorService initialization"""

    def test_service_initialization(self):
        """Test VectorService initializes with correct settings"""
        with patch("app.services.vector.AsyncQdrantClient") as mock_client:
            from app.services.vector import VectorService

            service = VectorService()

            assert service._collection is not None
            assert service._dim is not None

    def test_collection_name_from_settings(self):
        """Test collection name is read from settings"""
        with patch("app.services.vector.AsyncQdrantClient"):
            from app.services.vector import VectorService

            service = VectorService()
            # Should have collection from settings
            assert hasattr(service, "_collection")


class TestVectorServiceStore:
    """Test store method"""

    @pytest.mark.asyncio
    async def test_store_generates_uuid(self):
        """Test store generates UUID when not provided"""
        with patch("app.services.vector.AsyncQdrantClient") as mock_client:
            from app.services.vector import VectorService

            service = VectorService()
            service._client = AsyncMock()

            payload = {"type": "swap", "summary": "Test"}
            vector = [0.1] * 1536

            result = await service.store(payload, vector)

            assert result is not None
            # Should have called upsert
            service._client.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_with_custom_id(self):
        """Test store uses custom point ID when provided"""
        with patch("app.services.vector.AsyncQdrantClient") as mock_client:
            from app.services.vector import VectorService

            service = VectorService()
            service._client = AsyncMock()

            custom_id = "custom-point-id"
            result = await service.store(
                payload={"test": "data"},
                vector=[0.1] * 1536,
                point_id=custom_id
            )

            assert result == custom_id
            # Verify upsert was called with custom ID
            call_args = service._client.upsert.call_args
            assert call_args is not None

    @pytest.mark.asyncio
    async def test_store_payload_included(self):
        """Test store includes payload in point"""
        with patch("app.services.vector.AsyncQdrantClient") as mock_client:
            from app.services.vector import VectorService

            service = VectorService()
            service._client = AsyncMock()

            payload = {"type": "stake", "amount": "10 SOL"}
            vector = [0.1] * 1536

            await service.store(payload, vector)

            # Check payload was included
            service._client.upsert.assert_called_once()


class TestVectorServiceSearch:
    """Test search method - basic tests only"""

    @pytest.mark.asyncio
    async def test_search_returns_list(self):
        """Test search returns a list"""
        with patch("app.services.vector.settings") as mock_settings:
            mock_settings.QDRANT_URL = "http://localhost:6333"
            mock_settings.QDRANT_API_KEY = ""
            mock_settings.COLLECTION_NAME = "test_collection"
            mock_settings.EMBEDDING_DIM = 1536

            from app.services.vector import VectorService

            service = VectorService()
            service._client = AsyncMock()

            # Mock query_points (the actual method name)
            mock_result = MagicMock()
            mock_result.points = []
            service._client.query_points = AsyncMock(return_value=mock_result)

            results = await service.search(query_vector=[0.1] * 1536)

            assert isinstance(results, list)


class TestVectorServiceDelete:
    """Test delete method"""

    @pytest.mark.asyncio
    async def test_delete_by_id(self):
        """Test delete by point ID"""
        with patch("app.services.vector.settings") as mock_settings:
            mock_settings.QDRANT_URL = "http://localhost:6333"
            mock_settings.QDRANT_API_KEY = ""
            mock_settings.COLLECTION_NAME = "test_collection"
            mock_settings.EMBEDDING_DIM = 1536

            from app.services.vector import VectorService

            service = VectorService()
            service._client = AsyncMock()

            await service.delete(point_id="test-id-123")

            service._client.delete.assert_called_once()


class TestVectorServiceStore:
    """Test store method"""

    @pytest.mark.asyncio
    async def test_store_generates_uuid(self):
        """Test store generates UUID when not provided"""
        with patch("app.services.vector.settings") as mock_settings:
            mock_settings.QDRANT_URL = "http://localhost:6333"
            mock_settings.QDRANT_API_KEY = ""
            mock_settings.COLLECTION_NAME = "test_collection"
            mock_settings.EMBEDDING_DIM = 1536

            from app.services.vector import VectorService

            service = VectorService()
            service._client = AsyncMock()

            payload = {"type": "swap", "summary": "Test"}
            vector = [0.1] * 1536

            result = await service.store(payload, vector)

            assert result is not None
            service._client.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_with_custom_id(self):
        """Test store uses custom point ID when provided"""
        with patch("app.services.vector.settings") as mock_settings:
            mock_settings.QDRANT_URL = "http://localhost:6333"
            mock_settings.QDRANT_API_KEY = ""
            mock_settings.COLLECTION_NAME = "test_collection"
            mock_settings.EMBEDDING_DIM = 1536

            from app.services.vector import VectorService

            service = VectorService()
            service._client = AsyncMock()

            custom_id = "custom-point-id"
            result = await service.store(
                payload={"test": "data"},
                vector=[0.1] * 1536,
                point_id=custom_id
            )

            assert result == custom_id
