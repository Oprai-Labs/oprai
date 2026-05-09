"""Content-hash deduplication for crawled chunks.

A chunk is considered unchanged if its sha256 of normalised text matches the
stored hash. This prevents re-embedding + re-upsert on unchanged pages.
"""

from __future__ import annotations

import hashlib


def content_hash(text: str) -> str:
    """sha256 of UTF-8 encoded text — dedup key."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
