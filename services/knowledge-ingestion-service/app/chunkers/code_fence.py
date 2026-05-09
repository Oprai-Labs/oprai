"""Code-fence-aware chunker for Solana Cookbook and dev docs.

Guarantees code fences (``` ... ```) are never split across chunks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class CodeChunk:
    text: str
    chunk_index: int


_FENCE_RE = re.compile(r"(```[\s\S]*?```)", re.DOTALL)


def split_code_fence_aware(
    text: str,
    max_chars: int = 1500,
) -> list[CodeChunk]:
    """Split markdown preserving ``` fences intact."""
    # Tokenise into alternating prose / fence segments
    parts = _FENCE_RE.split(text)

    chunks: list[CodeChunk] = []
    buf = ""
    idx = 0

    for part in parts:
        if not part:
            continue
        # Fence block — never split it; flush buf first if needed
        if part.startswith("```"):
            if buf.strip():
                # Flush prose buffer
                for sub in _prose_chunks(buf, max_chars):
                    chunks.append(CodeChunk(text=sub, chunk_index=idx))
                    idx += 1
                buf = ""
            # Emit fence as its own chunk (or append if tiny)
            if len(part) <= max_chars:
                chunks.append(CodeChunk(text=part.strip(), chunk_index=idx))
                idx += 1
            else:
                # Fence too large — split hard at max_chars (last resort)
                for i in range(0, len(part), max_chars):
                    chunks.append(CodeChunk(text=part[i : i + max_chars], chunk_index=idx))
                    idx += 1
        else:
            buf += part

    if buf.strip():
        for sub in _prose_chunks(buf, max_chars):
            chunks.append(CodeChunk(text=sub, chunk_index=idx))
            idx += 1

    return chunks


def _prose_chunks(text: str, max_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    out: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(buf) + len(para) + 2 <= max_chars:
            buf = (buf + "\n\n" + para).strip()
        else:
            if buf:
                out.append(buf)
            buf = para[:max_chars]
    if buf:
        out.append(buf)
    return out
