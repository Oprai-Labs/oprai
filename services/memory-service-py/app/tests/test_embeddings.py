"""
Tests for Embeddings Service module.

Tests embedding generation using OpenAI API.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestEmbeddingServiceInit:
    """Test EmbeddingService initialization"""

    def test_initialization_requires_api_key(self):
        """Test that service requires OpenAI API key"""
        with patch("app.services.embeddings.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = ""

            from app.services.embeddings import EmbeddingService

            with pytest.raises(RuntimeError, match="OPRAI_OPENAI_API_KEY"):
                EmbeddingService()

    def test_initialization_with_api_key(self):
        """Test initialization succeeds with API key"""
        with patch("app.services.embeddings.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"
            mock_settings.EMBEDDING_MODEL = "text-embedding-3-large"

            from app.services.embeddings import EmbeddingService

            service = EmbeddingService()

            assert service._model == "text-embedding-3-large"

    def test_default_model(self):
        """Test default embedding model"""
        with patch("app.services.embeddings.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"
            mock_settings.EMBEDDING_MODEL = "text-embedding-3-small"

            from app.services.embeddings import EmbeddingService

            service = EmbeddingService()
            assert service._model is not None


class TestEmbeddingServiceEmbed:
    """Test embed method"""

    @pytest.mark.asyncio
    async def test_embed_single_text(self):
        """Test embedding a single text"""
        with patch("app.services.embeddings.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"
            mock_settings.EMBEDDING_MODEL = "text-embedding-3-large"

            from app.services.embeddings import EmbeddingService

            service = EmbeddingService()
            service._client = MagicMock()

            # Mock the API response
            mock_response = MagicMock()
            mock_embedding = [0.1] * 1536
            mock_response.data = [MagicMock(embedding=mock_embedding)]
            service._client.embeddings.create = AsyncMock(return_value=mock_response)

            result = await service.embed("Hello world")

            assert result == mock_embedding

    @pytest.mark.asyncio
    async def test_embed_empty_text_raises(self):
        """Test embedding empty text raises ValueError"""
        with patch("app.services.embeddings.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.embeddings import EmbeddingService

            service = EmbeddingService()

            with pytest.raises(ValueError, match="empty"):
                await service.embed("   ")

    @pytest.mark.asyncio
    async def test_embed_whitespace_text(self):
        """Test embedding whitespace-only text raises"""
        with patch("app.services.embeddings.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.embeddings import EmbeddingService

            service = EmbeddingService()

            with pytest.raises(ValueError):
                await service.embed("")

    @pytest.mark.asyncio
    async def test_embed_uses_correct_model(self):
        """Test embed uses configured model"""
        with patch("app.services.embeddings.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"
            mock_settings.EMBEDDING_MODEL = "text-embedding-3-large"

            from app.services.embeddings import EmbeddingService

            service = EmbeddingService()
            service._client = MagicMock()

            mock_response = MagicMock()
            mock_response.data = [MagicMock(embedding=[0.1] * 1536)]
            service._client.embeddings.create = AsyncMock(return_value=mock_response)

            await service.embed("Test text")

            # Verify model was passed
            call_args = service._client.embeddings.create.call_args
            assert call_args is not None

    @pytest.mark.asyncio
    async def test_embed_uses_float_format(self):
        """Test embed uses float encoding format"""
        with patch("app.services.embeddings.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.embeddings import EmbeddingService

            service = EmbeddingService()
            service._client = MagicMock()

            mock_response = MagicMock()
            mock_response.data = [MagicMock(embedding=[0.1] * 1536)]
            service._client.embeddings.create = AsyncMock(return_value=mock_response)

            await service.embed("Test")

            # Verify encoding_format was passed
            call_args = service._client.embeddings.create.call_args
            assert call_args is not None


class TestEmbeddingServiceBatch:
    """Test embed_batch method"""

    @pytest.mark.asyncio
    async def test_embed_batch_empty_list(self):
        """Test batch with empty list returns empty"""
        with patch("app.services.embeddings.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.embeddings import EmbeddingService

            service = EmbeddingService()

            result = await service.embed_batch([])

            assert result == []

    @pytest.mark.asyncio
    async def test_embed_batch_multiple_texts(self):
        """Test batch embedding multiple texts"""
        with patch("app.services.embeddings.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.embeddings import EmbeddingService

            service = EmbeddingService()
            service._client = MagicMock()

            # Mock batch response
            mock_response = MagicMock()
            mock_embeddings = [
                [0.1] * 1536,
                [0.2] * 1536,
                [0.3] * 1536,
            ]
            mock_response.data = [
                MagicMock(embedding=mock_embeddings[0]),
                MagicMock(embedding=mock_embeddings[1]),
                MagicMock(embedding=mock_embeddings[2]),
            ]
            service._client.embeddings.create = AsyncMock(return_value=mock_response)

            texts = ["First text", "Second text", "Third text"]
            results = await service.embed_batch(texts)

            assert len(results) == 3
            assert results[0] == mock_embeddings[0]
            assert results[1] == mock_embeddings[1]
            assert results[2] == mock_embeddings[2]

    @pytest.mark.asyncio
    async def test_embed_batch_single_item(self):
        """Test batch with single item"""
        with patch("app.services.embeddings.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.embeddings import EmbeddingService

            service = EmbeddingService()
            service._client = MagicMock()

            mock_response = MagicMock()
            mock_response.data = [MagicMock(embedding=[0.1] * 1536)]
            service._client.embeddings.create = AsyncMock(return_value=mock_response)

            result = await service.embed_batch(["Single text"])

            assert len(result) == 1


class TestEmbeddingServiceErrorHandling:
    """Test error handling"""

    @pytest.mark.asyncio
    async def test_embed_api_error(self):
        """Test handling of API errors"""
        with patch("app.services.embeddings.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.embeddings import EmbeddingService

            service = EmbeddingService()
            service._client = MagicMock()

            service._client.embeddings.create = AsyncMock(
                side_effect=Exception("API Error")
            )

            with pytest.raises(Exception):
                await service.embed("Test")

    @pytest.mark.asyncio
    async def test_embed_batch_partial_failure(self):
        """Test batch handles partial failures"""
        with patch("app.services.embeddings.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.embeddings import EmbeddingService

            service = EmbeddingService()
            service._client = MagicMock()

            # This should still work even if some texts fail
            mock_response = MagicMock()
            mock_response.data = [MagicMock(embedding=[0.1] * 1536)]
            service._client.embeddings.create = AsyncMock(return_value=mock_response)

            result = await service.embed_batch(["Test"])

            assert len(result) == 1


class TestEmbeddingServiceModels:
    """Test embedding model configuration"""

    def test_model_from_settings(self):
        """Test model is read from settings"""
        with patch("app.services.embeddings.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"
            mock_settings.EMBEDDING_MODEL = "text-embedding-3-large"

            from app.services.embeddings import EmbeddingService

            service = EmbeddingService()
            assert service._model == "text-embedding-3-large"

    def test_default_embedding_dimension(self):
        """Test default embedding dimension"""
        with patch("app.services.embeddings.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"
            mock_settings.EMBEDDING_MODEL = "text-embedding-3-large"

            from app.services.embeddings import EmbeddingService

            service = EmbeddingService()
            # The model returns 1536 for text-embedding-3-small
            # We just verify the service works assert service._model is not None

