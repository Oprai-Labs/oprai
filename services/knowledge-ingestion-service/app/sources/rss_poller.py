"""RSS feed poller — fetches and parses RSS/Atom feeds.

Stores only excerpt (≤500 chars) + link to respect news site ToS.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

import feedparser
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.sources.base import DocumentRef, RawDocument, Source, SourceConfig

logger = logging.getLogger(__name__)

UA = "OPRAI-Knowledge/1.0 (+https://oprai.io/bot)"
MAX_EXCERPT_CHARS = 500


def _entry_to_id(source_id: str, entry_id: str) -> str:
    safe = entry_id.replace("://", "_").replace("/", "_").replace(".", "_")
    return f"{source_id}.{safe}"[:200]


def _parse_date(entry: dict) -> Optional[datetime]:
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    except Exception:
        pass
    return None


def _excerpt(entry: dict) -> str:
    """Extract ≤500 char excerpt from RSS entry — never full article text."""
    summary = getattr(entry, "summary", "") or ""
    if len(summary) > MAX_EXCERPT_CHARS:
        summary = summary[:MAX_EXCERPT_CHARS].rsplit(" ", 1)[0] + "…"
    return summary.strip()


class RSSPoller(Source):
    async def discover(self, cfg: SourceConfig) -> AsyncIterator[DocumentRef]:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True) as client:
            try:
                resp = await client.get(cfg.base_url, timeout=30)
                resp.raise_for_status()
            except Exception as e:
                logger.error("RSS fetch failed %s: %s", cfg.base_url, e)
                return

            feed = feedparser.parse(resp.text)
            for entry in feed.entries:
                entry_id = getattr(entry, "id", None) or getattr(entry, "link", "")
                if not entry_id:
                    continue
                doc_id = _entry_to_id(cfg.id, entry_id)
                url = getattr(entry, "link", cfg.base_url)
                published = _parse_date(entry)
                yield DocumentRef(doc_id=doc_id, url=url)
                await asyncio.sleep(0.05)  # small delay between entries

    async def fetch(self, ref: DocumentRef, cfg: SourceConfig) -> Optional[RawDocument]:
        # For RSS we already have summary in discover(); re-fetch feed to get entry.
        # Simpler: fetch the feed again (cached by httpx) and find the matching entry.
        async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True) as client:
            try:
                resp = await client.get(cfg.base_url, timeout=30)
                resp.raise_for_status()
            except Exception as e:
                logger.warning("RSS re-fetch failed: %s", e)
                return None

            feed = feedparser.parse(resp.text)
            for entry in feed.entries:
                entry_id = getattr(entry, "id", None) or getattr(entry, "link", "")
                if _entry_to_id(cfg.id, entry_id) == ref.doc_id:
                    excerpt = _excerpt(entry)
                    title = getattr(entry, "title", "")
                    published = _parse_date(entry)
                    # Prepend link so model can cite the source URL
                    body = f"Source: {ref.url}\n\n{excerpt}"
                    return RawDocument(
                        ref=ref,
                        content_type="rss_item",
                        body=body,
                        title=title,
                        published_at=published,
                    )
        return None
