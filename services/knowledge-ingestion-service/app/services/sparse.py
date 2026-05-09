"""Sparse vector generation for Qdrant IDF modifier.

Qdrant's IDF modifier computes BM25 IDF server-side; the client only needs to
send token indices and their raw TF counts.  We use a simple lowercase +
alphanumeric tokeniser shared between ingestion and query sides.

The vocabulary is implicit — Qdrant builds it from all upserted sparse vectors.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import NamedTuple


_TOKEN_RE = re.compile(r"[a-z0-9]+")


class SparseVector(NamedTuple):
    indices: list[int]
    values: list[float]


def tokenise(text: str) -> list[str]:
    """Lowercase + alphanumeric split.  Shared between ingestion and query."""
    return _TOKEN_RE.findall(text.lower())


def _stable_token_id(token: str) -> int:
    """Map token string → stable non-negative integer.

    Uses Python's built-in hash with a fixed seed via djb2 variant so IDs are
    consistent across processes without an external vocabulary file.
    """
    h = 5381
    for ch in token:
        h = ((h << 5) + h) + ord(ch)
    return h & 0x7FFFFFFF  # keep positive, 31 bits → 2B vocabulary


def make_sparse_vector(text: str) -> SparseVector:
    """Build a sparse TF vector from text for Qdrant IDF modifier.

    Returns (indices, values) where values are raw term-frequency counts.
    Qdrant applies IDF weighting server-side when Modifier.IDF is set on the
    collection's sparse vector config.
    """
    tokens = tokenise(text)
    if not tokens:
        return SparseVector(indices=[], values=[])

    tf: Counter[int] = Counter(_stable_token_id(t) for t in tokens)
    indices = list(tf.keys())
    values = [float(v) for v in tf.values()]
    return SparseVector(indices=indices, values=values)
