"""HTML → clean Markdown normaliser.

Strips navigation, ads, headers, and footers via readability-lxml, then
converts the remaining article HTML to Markdown via markdownify.
"""

from __future__ import annotations

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_BLANK_LINES_RE = re.compile(r"\n{3,}")
_LEADING_HASH_RE = re.compile(r"^#{4,}", re.MULTILINE)


def html_to_markdown(html: str, base_url: str = "") -> str:
    """Extract main article content from HTML and convert to Markdown."""
    try:
        from readability import Document
        doc = Document(html, url=base_url)
        article_html = doc.summary(html_partial=True)
    except Exception:
        logger.debug("readability failed, falling back to raw HTML", exc_info=True)
        article_html = html

    try:
        import markdownify
        md = markdownify.markdownify(
            article_html,
            heading_style="ATX",
            strip=["script", "style", "nav", "footer", "aside", "noscript"],
        )
    except Exception:
        logger.debug("markdownify failed, using plain text", exc_info=True)
        from html.parser import HTMLParser

        class _Strip(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.text: list[str] = []
            def handle_data(self, data: str) -> None:
                self.text.append(data)

        p = _Strip()
        p.feed(article_html)
        md = " ".join(p.text)

    # Clean up excess whitespace
    md = _BLANK_LINES_RE.sub("\n\n", md)
    md = _LEADING_HASH_RE.sub("###", md)
    return md.strip()


def markdown_clean(md: str) -> str:
    """Light cleanup on already-markdown content (cookbook, GitHub READMEs)."""
    md = _BLANK_LINES_RE.sub("\n\n", md)
    return md.strip()
