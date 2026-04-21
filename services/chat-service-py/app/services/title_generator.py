"""Auto-generate chat session titles from the first user message."""

import logging
from pathlib import Path

from app.services.llm import LLMService

logger = logging.getLogger(__name__)

_TITLE_PROMPT: str | None = None


def _get_title_prompt() -> str:
    global _TITLE_PROMPT
    if _TITLE_PROMPT is None:
        path = Path(__file__).parent.parent / "prompts" / "generate_title.txt"
        _TITLE_PROMPT = path.read_text(encoding="utf-8")
    return _TITLE_PROMPT


async def generate_title(user_content: str) -> str:
    """Call the LLM to produce a short session title.

    Falls back to a truncated version of the user message on failure.
    """
    try:
        llm = LLMService()
        title = await llm.acomplete([
            {"role": "system", "content": _get_title_prompt()},
            {"role": "user", "content": user_content},
        ])
        title = title.strip().strip('"').strip("'")
        if title:
            return title[:60]
    except Exception:
        logger.warning("Title generation failed, using fallback", exc_info=True)

    return user_content[:40]
