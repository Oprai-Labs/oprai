"""OpenAI embedding service with batching, retry, and cost tracking."""

from __future__ import annotations

import logging
from typing import Optional

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)


class Embedder:
    """Batched OpenAI text-embedding-3-large embedder.

    Sends texts in batches of EMBEDDING_BATCH_SIZE to stay within API limits.
    Retries with exponential backoff on rate-limit / server errors.
    Tracks total token usage for cost budgeting.
    """

    def __init__(self) -> None:
        self._client = AsyncOpenAI(api_key=settings.OPRAI_OPENAI_API_KEY)
        self._total_tokens = 0

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        reraise=True,
    )
    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=texts,
        )
        self._total_tokens += resp.usage.total_tokens
        return [d.embedding for d in sorted(resp.data, key=lambda x: x.index)]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts, batching automatically."""
        batch_size = settings.EMBEDDING_BATCH_SIZE
        all_vecs: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            vecs = await self._embed_batch(batch)
            all_vecs.extend(vecs)
        logger.debug("Embedded %d texts, running total tokens: %d", len(texts), self._total_tokens)
        return all_vecs

    async def embed_one(self, text: str) -> list[float]:
        vecs = await self.embed_many([text])
        return vecs[0]
