"""Markdown header-aware chunker.

Splits on ## and ### boundaries.  Each chunk gets the heading breadcrumb as
section_path.  Never exceeds max_chars; soft target is soft_chars.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class MarkdownChunk:
    text: str
    section_path: str
    chunk_index: int


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def split_markdown(
    text: str,
    max_chars: int = 1200,
    soft_chars: int = 800,
) -> list[MarkdownChunk]:
    """Split markdown text on heading boundaries, respecting char limits."""
    # Find all heading positions
    headings = [(m.start(), m.group(1), m.group(2)) for m in _HEADING_RE.finditer(text)]

    if not headings:
        # No headings — paragraph split
        return _paragraph_split(text, max_chars)

    sections: list[tuple[str, str]] = []  # (section_path, content)
    breadcrumb: dict[int, str] = {}

    for i, (pos, hashes, title) in enumerate(headings):
        level = len(hashes)
        breadcrumb[level] = title
        # Clear deeper levels
        for deeper in list(breadcrumb.keys()):
            if deeper > level:
                del breadcrumb[deeper]

        end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        content = text[pos:end].strip()
        path = " > ".join(breadcrumb[l] for l in sorted(breadcrumb))
        sections.append((path, content))

    chunks: list[MarkdownChunk] = []
    idx = 0

    for path, content in sections:
        if len(content) <= max_chars:
            if content.strip():
                chunks.append(MarkdownChunk(text=content, section_path=path, chunk_index=idx))
                idx += 1
        else:
            # Sub-split by paragraphs
            for sub in _paragraph_split(content, max_chars):
                chunks.append(MarkdownChunk(
                    text=sub.text,
                    section_path=path,
                    chunk_index=idx,
                ))
                idx += 1

    return chunks


def _paragraph_split(text: str, max_chars: int) -> list[MarkdownChunk]:
    paragraphs = re.split(r"\n\n+", text)
    chunks: list[MarkdownChunk] = []
    buf = ""
    idx = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(buf) + len(para) + 2 <= max_chars:
            buf = (buf + "\n\n" + para).strip()
        else:
            if buf:
                chunks.append(MarkdownChunk(text=buf, section_path="", chunk_index=idx))
                idx += 1
            buf = para[:max_chars]

    if buf:
        chunks.append(MarkdownChunk(text=buf, section_path="", chunk_index=idx))

    return chunks
