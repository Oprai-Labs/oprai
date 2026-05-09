"""Sentence-window chunker for glossary / short-form content.

Produces 3-sentence windows with stride 1.  Ideal for term definitions where
each sentence carries high information density.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class SentenceChunk:
    text: str
    chunk_index: int


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def split_sentence_window(
    text: str,
    window: int = 3,
    stride: int = 1,
) -> list[SentenceChunk]:
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    if not sentences:
        return []

    chunks: list[SentenceChunk] = []
    i = 0
    idx = 0
    while i < len(sentences):
        window_sents = sentences[i : i + window]
        chunk_text = " ".join(window_sents)
        chunks.append(SentenceChunk(text=chunk_text, chunk_index=idx))
        idx += 1
        i += stride

    return chunks
