"""Paragraph-based chunker (adapted from opraios/core/knowledge_base.py:TextChunker).

Used for: HTML articles, RSS items, general prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ParagraphChunk:
    text: str
    chunk_index: int


def split_paragraphs(
    text: str,
    max_words: int = 1000,
    overlap_words: int = 200,
) -> list[ParagraphChunk]:
    """Split text into overlapping word-count chunks, respecting paragraph breaks."""
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]

    chunks: list[ParagraphChunk] = []
    current_words: list[str] = []
    idx = 0

    for para in paragraphs:
        words = para.split()
        if len(current_words) + len(words) <= max_words:
            current_words.extend(words)
        else:
            if current_words:
                chunks.append(ParagraphChunk(text=" ".join(current_words), chunk_index=idx))
                idx += 1
                # Overlap: keep tail of previous chunk
                current_words = current_words[-overlap_words:] + words
            else:
                # Single paragraph larger than max_words — hard split
                while words:
                    batch = words[:max_words]
                    chunks.append(ParagraphChunk(text=" ".join(batch), chunk_index=idx))
                    idx += 1
                    words = words[max_words - overlap_words:]
                current_words = []

    if current_words:
        chunks.append(ParagraphChunk(text=" ".join(current_words), chunk_index=idx))

    return chunks
