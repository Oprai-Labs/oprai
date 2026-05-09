"""Routes RawDocument to the correct chunker based on content_type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.chunkers.code_fence import CodeChunk, split_code_fence_aware
from app.chunkers.markdown_header import MarkdownChunk, split_markdown
from app.chunkers.paragraph import ParagraphChunk, split_paragraphs
from app.chunkers.sentence_window import SentenceChunk, split_sentence_window
from app.sources.base import RawDocument


@dataclass
class ChunkResult:
    text: str
    chunk_index: int
    section_path: str


def chunk_document(doc: RawDocument) -> list[ChunkResult]:
    ct = doc.content_type
    body = doc.body

    if ct == "html":
        # Already converted to markdown by normaliser
        chunks = split_paragraphs(body, max_words=1000, overlap_words=200)
        return [ChunkResult(text=c.text, chunk_index=c.chunk_index, section_path="") for c in chunks]

    elif ct == "markdown":
        chunks = split_markdown(body, max_chars=1200, soft_chars=800)
        return [ChunkResult(text=c.text, chunk_index=c.chunk_index, section_path=c.section_path) for c in chunks]

    elif ct == "rss_item":
        chunks = split_paragraphs(body, max_words=800, overlap_words=100)
        return [ChunkResult(text=c.text, chunk_index=c.chunk_index, section_path="") for c in chunks]

    elif ct == "code":
        chunks = split_code_fence_aware(body, max_chars=1500)
        return [ChunkResult(text=c.text, chunk_index=c.chunk_index, section_path="") for c in chunks]

    elif ct == "json":
        # One chunk per document (DeFiLlama protocol records, etc.)
        return [ChunkResult(text=body, chunk_index=0, section_path="")]

    else:
        # Fallback: paragraph split
        chunks = split_paragraphs(body, max_words=1000, overlap_words=200)
        return [ChunkResult(text=c.text, chunk_index=c.chunk_index, section_path="") for c in chunks]
