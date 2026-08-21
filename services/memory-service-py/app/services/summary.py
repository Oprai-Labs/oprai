"""Conversation summarization using OpenAI.

Takes a conversation chunk and produces a structured summary with
title, bullet points, and metadata.
"""

import logging
from datetime import UTC, datetime, timezone

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

SUMMARY_FORMAT_HINT = (
    "Title: <...> | Date: YYYY-MM-DD | Summary: * bullet * bullet * bullet"
)

SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a conversation summarizer for a DeFi AI assistant on Solana. "
    "Summarize the conversation chunk into a concise summary following this format:\n"
    f"{SUMMARY_FORMAT_HINT}\n\n"
    "Focus on: actions taken, tokens/protocols discussed, user preferences expressed, "
    "decisions made. Keep it under 200 words."
)


class SummaryService:
    """Generate summaries for conversation chunks."""

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None
        if settings.OPRAI_OPENAI_API_KEY:
            self._client = AsyncOpenAI(api_key=settings.OPRAI_OPENAI_API_KEY)

    async def summarize(
        self,
        conversation_id: str,
        chunk: str,
        token_count: int = 0,
    ) -> dict:
        """Produce a structured summary of a conversation chunk.

        Parameters
        ----------
        conversation_id:
            The session / conversation ID for audit purposes.
        chunk:
            The raw conversation text to summarize.
        token_count:
            Approximate token count of the chunk.

        Returns
        -------
        dict
            ``{"title": str, "summary": str, "tokenCount": int, "timestamp": str}``
        """
        now = datetime.now(UTC).isoformat()

        if not self._client:
            logger.warning("No OpenAI API key -- returning fallback summary")
            return self._fallback(chunk, token_count, now)

        try:
            response = await self._client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": SUMMARIZATION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Summarize this conversation chunk "
                            f"({token_count} tokens):\n\n{chunk}"
                        ),
                    },
                ],
                max_tokens=300,
                temperature=0.3,
            )

            summary_text = (response.choices[0].message.content or "").strip()

            if not summary_text:
                return self._fallback(chunk, token_count, now)

            # Extract title from the summary if present
            title = "Session summary"
            if summary_text.startswith("Title:"):
                parts = summary_text.split("|", 1)
                title = parts[0].replace("Title:", "").strip() or title

            return {
                "title": title,
                "summary": summary_text,
                "tokenCount": token_count,
                "timestamp": now,
            }

        except Exception:
            logger.exception("Summarization error for conversation %s", conversation_id)
            return self._fallback(chunk, token_count, now)

    @staticmethod
    def _fallback(chunk: str, token_count: int, timestamp: str) -> dict:
        """Produce a basic fallback summary from the raw chunk."""
        preview = chunk[:200].replace("\n", " ").strip()
        return {
            "title": "Session summary",
            "summary": (
                f"Title: Session Summary | "
                f"Date: {timestamp[:10]} | "
                f"Summary: * {preview}..."
            ),
            "tokenCount": token_count,
            "timestamp": timestamp,
        }
