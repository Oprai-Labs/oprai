"""Embedding generation using OpenAI text-embedding-3-large.

Provides a thin async wrapper over the OpenAI embeddings API
so callers just pass text and get a vector back.
"""

import logging

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Generate embeddings via OpenAI."""

    def __init__(self) -> None:
        if not settings.OPRAI_OPENAI_API_KEY:
            raise RuntimeError(
                "Embedding service requires OPRAI_OPENAI_API_KEY to be set"
            )
        self._client = AsyncOpenAI(api_key=settings.OPRAI_OPENAI_API_KEY)
        self._model = settings.EMBEDDING_MODEL

    async def embed(self, text: str) -> list[float]:
        """Generate an embedding vector for the given text.

        Parameters
        ----------
        text:
            The input text to embed.  Should be non-empty.

        Returns
        -------
        list[float]
            A vector of length ``EMBEDDING_DIM`` (default 3072).
        """
        if not text.strip():
            raise ValueError("Cannot embed empty text")

        response = await self._client.embeddings.create(
            model=self._model,
            input=text,
            encoding_format="float",
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts.

        Parameters
        ----------
        texts:
            List of input strings.

        Returns
        -------
        list[list[float]]
            One vector per input text, in the same order.
        """
        if not texts:
            return []

        response = await self._client.embeddings.create(
            model=self._model,
            input=texts,
            encoding_format="float",
        )
        return [item.embedding for item in response.data]
