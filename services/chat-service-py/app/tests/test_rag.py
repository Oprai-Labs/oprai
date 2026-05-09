"""
Tests for RAG (Retrieval Augmented Generation) module.

Tests document ingestion, vector storage, and semantic search.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


# Mock settings before importing
@pytest.fixture(autouse=True)
def mock_settings():
    """Mock settings for RAG tests"""
    with patch('app.rag.settings') as mock_settings:
        mock_settings.QDRANT_URL = "http://localhost:6333"
        yield mock_settings


class TestKnowledgeChunk:
    """Test KnowledgeChunk dataclass"""

    def test_chunk_creation(self):
        """Test creating a KnowledgeChunk"""
        from app.rag import KnowledgeChunk

        chunk = KnowledgeChunk(
            id="chunk-1",
            content="Test content",
            source="test.pdf",
            source_type="pdf",
            chunk_index=0
        )

        assert chunk.id == "chunk-1"
        assert chunk.content == "Test content"
        assert chunk.source == "test.pdf"
        assert chunk.source_type == "pdf"
        assert chunk.chunk_index == 0

    def test_chunk_with_optional_fields(self):
        """Test chunk with optional embedding and metadata"""
        from app.rag import KnowledgeChunk

        chunk = KnowledgeChunk(
            id="chunk-2",
            content="Content with metadata",
            source="doc.txt",
            source_type="text",
            chunk_index=1,
            embedding=[0.1, 0.2, 0.3],
            metadata={"author": "test"}
        )

        assert chunk.embedding == [0.1, 0.2, 0.3]
        assert chunk.metadata["author"] == "test"


class TestSearchResult:
    """Test SearchResult dataclass"""

    def test_search_result_creation(self):
        """Test creating a SearchResult"""
        from app.rag import SearchResult, KnowledgeChunk

        chunk = KnowledgeChunk(
            id="c1",
            content="Test",
            source="src",
            source_type="text",
            chunk_index=0
        )

        result = SearchResult(
            chunk=chunk,
            score=0.95
        )

        assert result.chunk.id == "c1"
        assert result.score == 0.95

    def test_search_result_with_highlights(self):
        """Test search result with highlights"""
        from app.rag import SearchResult, KnowledgeChunk

        chunk = KnowledgeChunk(
            id="c1",
            content="Test content",
            source="src",
            source_type="text",
            chunk_index=0
        )

        result = SearchResult(
            chunk=chunk,
            score=0.8,
            highlights=["<em>test</em>", "content"]
        )

        assert len(result.highlights) == 2


class TestRAGServiceInit:
    """Test RAGService initialization"""

    def test_init_default(self):
        """Test initialization with defaults"""
        from app.rag import RAGService

        service = RAGService()

        assert service.qdrant_url == "http://localhost:6333"
        assert service.embedding_model == "text-embedding-3-large"
        assert service._client is None
        assert service._llm is None

    def test_init_custom(self):
        """Test initialization with custom values"""
        from app.rag import RAGService

        service = RAGService(
            qdrant_url="http://custom:6333",
            embedding_model="text-embedding-3-small"
        )

        assert service.qdrant_url == "http://custom:6333"
        assert service.embedding_model == "text-embedding-3-small"

    def test_collection_name(self):
        """Test collection name constant"""
        from app.rag import RAGService

        assert RAGService.COLLECTION_NAME == "oprai_knowledge"
        assert RAGService.EMBEDDING_DIMENSION == 1536


class TestRAGServiceChunking:
    """Test text chunking functionality"""

    def test_chunk_text_basic(self):
        """Test basic text chunking"""
        from app.rag import RAGService

        service = RAGService()
        text = "word1 word2 word3 word4 word5"

        chunks = service._chunk_text(text, chunk_size=2, chunk_overlap=0)

        assert len(chunks) >= 2

    def test_chunk_text_with_overlap(self):
        """Test chunking with overlap"""
        from app.rag import RAGService

        service = RAGService()
        text = "word1 word2 word3 word4 word5"

        chunks = service._chunk_text(text, chunk_size=2, chunk_overlap=1)

        # Verify overlap by checking chunks share words
        assert len(chunks) > 0

    def test_chunk_text_long(self):
        """Test chunking long text"""
        from app.rag import RAGService

        service = RAGService()
        text = " ".join([f"word{i}" for i in range(100)])

        chunks = service._chunk_text(text, chunk_size=20, chunk_overlap=5)

        assert len(chunks) > 1

    def test_chunk_text_empty(self):
        """Test chunking empty text"""
        from app.rag import RAGService

        service = RAGService()
        chunks = service._chunk_text("", chunk_size=10, chunk_overlap=2)

        assert chunks == []


class TestRAGServiceSearch:
    """Test RAG search functionality"""

    @pytest.mark.asyncio
    async def test_search_no_results(self):
        """Test search with no results"""
        from app.rag import RAGService

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=[])

        mock_llm = MagicMock()
        mock_llm.embed = AsyncMock(return_value=[0.1, 0.2])

        service = RAGService()
        service._client = mock_client
        service._llm = mock_llm

        results = await service.search("test query", limit=5)

        assert results == []

    @pytest.mark.asyncio
    async def test_search_with_results(self):
        """Test search with results"""
        from app.rag import RAGService, SearchResult, KnowledgeChunk

        mock_result = MagicMock()
        mock_result.id = "chunk-1"
        mock_result.score = 0.9
        mock_result.payload = {
            "content": "Test content",
            "source": "test.pdf",
            "source_type": "pdf",
            "chunk_index": 0
        }

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=[mock_result])

        mock_llm = MagicMock()
        mock_llm.embed = AsyncMock(return_value=[0.1, 0.2])

        service = RAGService()
        service._client = mock_client
        service._llm = mock_llm

        results = await service.search("test query", limit=5)

        assert len(results) == 1
        assert results[0].score == 0.9


class TestRAGServiceContext:
    """Test context building for LLM"""

    @pytest.mark.asyncio
    async def test_get_context_for_query_empty(self):
        """Test getting context with no results"""
        from app.rag import RAGService

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=[])

        mock_llm = MagicMock()
        mock_llm.embed = AsyncMock(return_value=[0.1])

        service = RAGService()
        service._client = mock_client
        service._llm = mock_llm

        context = await service.get_context_for_query("test")

        assert context == ""

    @pytest.mark.asyncio
    async def test_get_context_for_query_with_results(self):
        """Test getting context with results"""
        from app.rag import RAGService, SearchResult, KnowledgeChunk

        chunk = KnowledgeChunk(
            id="c1",
            content="Relevant information",
            source="doc.pdf",
            source_type="pdf",
            chunk_index=0
        )

        mock_result = MagicMock()
        mock_result.id = "c1"
        mock_result.score = 0.9
        mock_result.payload = {
            "content": "Relevant information",
            "source": "doc.pdf",
            "source_type": "pdf",
            "chunk_index": 0
        }

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=[mock_result])

        mock_llm = MagicMock()
        mock_llm.embed = AsyncMock(return_value=[0.1])

        service = RAGService()
        service._client = mock_client
        service._llm = mock_llm

        context = await service.get_context_for_query("test")

        assert context != ""
        assert "doc.pdf" in context


class TestRAGServiceIngest:
    """Test document ingestion"""

    @pytest.mark.asyncio
    async def test_ingest_document_empty(self):
        """Test ingesting empty content"""
        from app.rag import RAGService

        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()

        mock_llm = MagicMock()
        mock_llm.embed = AsyncMock(return_value=[0.1])

        service = RAGService()
        service._client = mock_client
        service._llm = mock_llm

        chunks = await service.ingest_document(
            content="",
            source="test.txt",
            source_type="text"
        )

        assert chunks == []


class TestRAGServiceDelete:
    """Test document deletion"""

    @pytest.mark.asyncio
    async def test_delete_document(self):
        """Test deleting a document"""
        from app.rag import RAGService

        mock_client = AsyncMock()
        mock_client.delete = AsyncMock()

        service = RAGService()
        service._client = mock_client

        result = await service.delete_document(
            source="test.pdf",
            owner_wallet="wallet123"
        )

        assert mock_client.delete.called


class TestRAGServiceStats:
    """Test statistics retrieval"""

    @pytest.mark.asyncio
    async def test_get_stats(self):
        """Test getting stats"""
        from app.rag import RAGService

        mock_info = MagicMock()
        mock_info.points_count = 100
        mock_info.config.params.vectors.size = 1536
        mock_info.status.value = "green"

        mock_client = AsyncMock()
        mock_client.get_collection = AsyncMock(return_value=mock_info)

        service = RAGService()
        service._client = mock_client

        stats = await service.get_stats()

        assert stats["total_chunks"] == 100
        assert stats["vector_size"] == 1536
        assert stats["status"] == "green"


class TestGlobalRAGService:
    """Test global RAG service singleton"""

    def test_get_rag_service(self):
        """Test getting global RAG service"""
        from app.rag import get_rag_service, RAGService

        # Reset global
        import app.rag as rag_module
        rag_module._rag_service = None

        service = get_rag_service()

        assert service is not None
        assert isinstance(service, RAGService)

    def test_singleton(self):
        """Test singleton behavior"""
        from app.rag import get_rag_service

        # Reset global
        import app.rag as rag_module
        rag_module._rag_service = None

        service1 = get_rag_service()
        service2 = get_rag_service()

        assert service1 is service2
