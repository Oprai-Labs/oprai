"""Auto-generate chat session titles from the first user message."""

import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_TITLE_PROMPT: str | None = None
_CLIENT = None


def _get_title_prompt() -> str:
    global _TITLE_PROMPT
    if _TITLE_PROMPT is None:
        path = Path(__file__).parent.parent / "prompts" / "generate_title.txt"
        _TITLE_PROMPT = path.read_text(encoding="utf-8")
    return _TITLE_PROMPT


def _fallback(user_content: str) -> str:
    """A clean, human title from the message itself — never 'New chat'."""
    t = " ".join((user_content or "").split()).strip()
    if len(t) > 48:
        t = t[:48].rstrip() + "…"
    return t or "New chat"


async def generate_title(user_content: str) -> str:
    """Produce a short session title from the first user message.

    Uses the cheap/fast model (OPRAI_SUMMARIZER_MODEL) rather than the responder
    so it can run early on the first turn without adding responder-tier latency.
    Always returns something usable — falls back to a cleaned truncation of the
    message, never an empty string or a bare 'New chat'.
    """
    content = (user_content or "").strip()
    if not content:
        return "New chat"

    api_key = (settings.OPRAI_OPENAI_API_KEY or "").strip()
    if not api_key:
        return _fallback(content)

    global _CLIENT
    if _CLIENT is None:
        try:
            from openai import AsyncOpenAI

            _CLIENT = AsyncOpenAI(api_key=api_key)
        except Exception:
            logger.debug("title_generator: OpenAI init failed", exc_info=True)
            return _fallback(content)

    try:
        resp = await _CLIENT.chat.completions.create(
            model=settings.OPRAI_SUMMARIZER_MODEL,
            messages=[
                {"role": "system", "content": _get_title_prompt()},
                {"role": "user", "content": content[:2000]},
            ],
            max_tokens=24,
            temperature=0.3,
        )
        title = (resp.choices[0].message.content or "").strip().strip('"').strip("'")
        if title:
            return title[:60]
    except Exception:
        logger.warning("Title generation failed, using fallback", exc_info=True)

    return _fallback(content)
