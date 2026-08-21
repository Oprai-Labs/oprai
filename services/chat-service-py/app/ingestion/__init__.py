"""
Document Ingestion System

Based on elizaOS document processing capabilities.

Provides document processing, PDF ingestion, URL scraping,
 audio transcription, video processing, and image analysis.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx
import pdfplumber
import PyPDF2
from bs4 import BeautifulSoup
from openai import OpenAI
from playwright.async_api import async_playwright

from app.config import settings
from app.plugins.base import PluginContext, PluginResult

logger = logging.getLogger(__name__)


class IngestionStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ERROR = "error"


class DocumentType(str, Enum):
    PDF = "pdf"
    URL = "url"
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    TEXT = "text"
    YOUTUBE = "youtube"
    TWITTER = "twitter"
    MARKDOWN = "markdown"
    HTML = "html"
    JSON = "json"
    CSV = "csv"
    UNKNOWN = "unknown"

    @property
    def max_file_size_mb(self) -> int:
        return 10

    @property
    def supported_types(self) -> list[DocumentType]:
        return [
            DocumentType.PDF,
            DocumentType.URL,
            DocumentType.VIDEO,
            DocumentType.AUDIO,
            DocumentType.IMAGE,
            DocumentType.TEXT,
            DocumentType.YOUTUBE,
            DocumentType.TWITTER,
            DocumentType.MARKDOWN,
            DocumentType.HTML,
            DocumentType.JSON,
            DocumentType.CSV,
        ]


class IngestionConfig(BaseSettings):
    """Configuration for document ingestion"""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    openai_model: str = "gpt-4o"
    anthropic_model: str = "claude-3-5-sonnet-20241031"
    gemini_model: str = "gemini-1.5-flash"
    ollama_base_url: str = ""
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    embedding_model: str = "text-embedding-3-large"
    embedding_dimensions: int = 1536
    chunk_size: int = 500
    chunk_overlap: int = 100
    max_workers: int = 4
    memory_collection: str = "ingested_documents"
    temp_document_dir: str = "/tmp/oprai_ingestion"
    log_retention_days: int = 30

    # Feature flags
    enable_youtube_transcription: bool = True
    enable_twitter_scraping: bool = True
    enable_video_transcription: bool = True
    enable_image_analysis: bool = True
    enable_audio_transcription: bool = True
    enable_semantic_chunking: bool = True
    use_ollama_for_local_processing: bool = False

    # API keys
    youtube_api_key: str = ""
    twitter_api_key: str = ""
    deepgram_token: str = ""
    openai_whisper_model: str = "whisper-1"

    # Base URLs
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    gemini_base_url: str = "https://api.deepseek.com"
    deepseek_base_url: str = "http://localhost:11434"
    deepgram_base_url: str = "https://api.deepgram.com"

    def validate(self) -> None:
        """Validate configuration"""
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for ingestion")
        if self.enable_youtube_transcription and not self.youtube_api_key:
            raise ValueError("YOUTUBE_API_KEY is required for YouTube transcription")
        if self.enable_twitter_scraping and not self.twitter_api_key:
            raise ValueError("TWITTER_API_KEY is required for Twitter scraping")
        if self.enable_video_transcription and not self.deepgram_token:
            raise ValueError("DEEPGRAM_TOKEN is required for video transcription")
        if self.enable_image_analysis and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for image analysis")
        if self.enable_audio_transcription and not self.deepgram_token:
            raise ValueError("DEEPGRAM_TOKEN is required for audio transcription")
        if self.use_ollama_for_local_processing and not self.ollama_base_url:
            raise ValueError("OLLAMA_BASE_URL is required for Ollama processing")


class IngestionService:
    """Document ingestion service"""

    def __init__(self, config: IngestionConfig, db_session=None):
        self.config = config
        self.db_session = db_session
        self.qdrant_client = None
        self.processing_queue: asyncio.Queue = None
        self._queue = None
        self._workers = 4
        self._shutdown = False
        self._processors: dict = {}
        logger.info(f"Initialized ingestion service with {self._workers} workers")

    async def process(
        self,
        file_path: Path,
        document_type: DocumentType,
        owner_wallet: str | None = None,
        metadata: dict[str, Any] | None = None
    ) -> dict:
        """Ingest a document and add to processing queue"""
        file_name = file.name
        file_size = file.stat().st_size
        file_type = file_type.value
        import uuid
        ingestion_id = str(uuid.uuid4())
        file_name = file_path.name
        file_type = document_type.value

        if self.processing_queue:
            await self.processing_queue.put((
                file_name,
                file_type,
                str(file_path),
                ingestion_id,
                datetime.utcnow(),
                metadata,
            ))

        logger.info(f"Queued document for ingestion: {file_path}")

        return {
            "ingestion_id": ingestion_id,
            "file_name": file_name,
            "file_type": file_type,
            "status": IngestionStatus.PENDING,
        }

    async def process_file(self, file_path: Path) -> dict:
        """Process a single document file"""
        file_type = self._detect_file_type(file_path)
        processor = self._processors.get(file_type)

        if not processor:
            logger.warning(f"No processor found for file type: {file_type}, using text processor")
            processor = self._processors.get("text")

        logger.info(f"Using {file_type} processor for {file_path}")

        return await processor.process(file_path) if processor else None

    def _detect_file_type(self, file_path: Path) -> str:
        """Detect file type from extension"""
        suffix = file_path.suffix.lower()
        type_map = {
            ".pdf": "pdf",
            ".txt": "text",
            ".md": "markdown",
            ".html": "html",
            ".json": "json",
            ".csv": "csv",
        }
        return type_map.get(suffix, "text")

    async def process_directory(self, directory: Path) -> list[Path]:
        """Recursively process all files in a directory"""
        file_paths = []
        for item in directory.iterdir():
            if item.is_file():
                file_paths.append(item)
            elif item.is_dir():
                file_paths.extend(await self.process_directory(item))

        return file_paths

    async def ingest(self, file_path: Path) -> dict:
        """Ingest a file and create chunks."""
        try:
            # Read file content
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Simple chunking
            chunks = []
            chunk_size = self.config.chunk_size
            for i in range(0, len(content), chunk_size):
                chunks.append(content[i:i + chunk_size])

            logger.info(f"Created {len(chunks)} chunks for {file_path}")

            return {
                "file_path": str(file_path),
                "chunks": chunks,
                "chunk_count": len(chunks),
            }

        except Exception as e:
            logger.error("Error ingesting {file_path}", exc_info=True)
            raise

    async def _extract_text_from_chunks(self, chunks: list) -> list[str]:
        """Extract text from a list of chunks"""
        texts = []
        for chunk in chunks:
            if hasattr(chunk, 'page_content'):
                texts.append(chunk.page_content.strip())
            elif isinstance(chunk, str):
                texts.append(chunk.strip())
        return texts

    async def _chunk_and_embed(self, chunks: list, source_file: str, owner_wallet: str) -> list:
        """Process chunks and create embeddings."""
        if not chunks:
            logger.warning("No chunks to embed")
            return []

        embeddings = []
        for i, chunk in enumerate(chunks):
            embeddings.append({
                "chunk_index": i,
                "content": chunk[:500] if isinstance(chunk, str) else str(chunk)[:500],
                "source_file": source_file,
                "owner_wallet": owner_wallet,
            })

        logger.info(f"Created {len(embeddings)} embeddings from {source_file}")
        return embeddings

    async def get_processed_files(self) -> list[dict]:
        """Get list of processed files with metadata"""
        return getattr(self, '_processed_files', [])

    async def get_processed_content(self, ingestion_id: str) -> str | None:
        """Get processed content for an ingestion"""
        processed = getattr(self, '_processed_files', {})
        if ingestion_id in processed:
            return processed[ingestion_id].get('content')
        return None

