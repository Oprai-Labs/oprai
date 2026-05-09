"""Documentation site crawler — sitemap.xml discovery + per-page fetch.

Respects robots.txt, crawl_delay, and per-host concurrency limits.
"""

from __future__ import annotations

import asyncio
import logging
import re
import urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import AsyncIterator, Optional
from urllib.parse import urljoin, urlparse

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.services.normaliser import html_to_markdown
from app.sources.base import DocumentRef, RawDocument, Source, SourceConfig

logger = logging.getLogger(__name__)

UA = "OPRAI-Knowledge/1.0 (+https://oprai.io/bot)"
_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
_robots_lock = asyncio.Lock()


async def _get_robots(base_url: str, client: httpx.AsyncClient) -> urllib.robotparser.RobotFileParser:
    parsed = urlparse(base_url)
    host = f"{parsed.scheme}://{parsed.netloc}"
    async with _robots_lock:
        if host not in _robots_cache:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = await client.get(urljoin(host, "/robots.txt"), timeout=10)
                rp.parse(r.text.splitlines())
            except Exception:
                rp.allow_all = True
            _robots_cache[host] = rp
        return _robots_cache[host]


def _slug(url: str, source_id: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/").replace("/", "_").replace("-", "_")
    return f"{source_id}.{path}" if path else source_id


def _matches_patterns(url: str, include: list[str], exclude: list[str]) -> bool:
    for pat in exclude:
        if re.search(pat, url):
            return False
    if include:
        return any(re.search(pat, url) for pat in include)
    return True


class DocsCrawler(Source):
    """Crawl a docs site via its sitemap.xml."""

    async def discover(self, cfg: SourceConfig) -> AsyncIterator[DocumentRef]:
        sitemap_url = cfg.extra.get("sitemap_url") or urljoin(cfg.base_url, "/sitemap.xml")
        async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True) as client:
            try:
                resp = await client.get(sitemap_url, timeout=30)
                resp.raise_for_status()
            except Exception as e:
                logger.error("Failed to fetch sitemap %s: %s", sitemap_url, e)
                return

            urls = _parse_sitemap(resp.text, sitemap_url)
            count = 0
            for url in urls:
                if count >= cfg.max_pages:
                    break
                if not _matches_patterns(url, cfg.include_patterns, cfg.exclude_patterns):
                    continue
                rp = await _get_robots(url, client)
                if not rp.can_fetch(UA, url):
                    logger.debug("robots.txt blocked: %s", url)
                    continue
                yield DocumentRef(doc_id=_slug(url, cfg.id), url=url)
                count += 1
                await asyncio.sleep(cfg.crawl_delay_s)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
    async def fetch(self, ref: DocumentRef, cfg: SourceConfig) -> Optional[RawDocument]:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True) as client:
            try:
                resp = await client.get(ref.url, timeout=30)
                resp.raise_for_status()
            except Exception as e:
                logger.warning("Fetch failed %s: %s", ref.url, e)
                return None

            content_type = resp.headers.get("content-type", "")
            if "html" in content_type:
                body = html_to_markdown(resp.text, base_url=ref.url)
                ct = "html"
            else:
                body = resp.text
                ct = "markdown"

            title = _extract_title(resp.text)

            return RawDocument(
                ref=ref,
                content_type=ct,
                body=body,
                title=title,
            )


def _parse_sitemap(xml_text: str, base_url: str) -> list[str]:
    urls: list[str] = []
    try:
        root = ET.fromstring(xml_text)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        # Check if this is a sitemap index
        for sitemap_tag in root.findall("sm:sitemap/sm:loc", ns):
            urls.append(sitemap_tag.text.strip())

        # Or a regular sitemap
        for url_tag in root.findall("sm:url/sm:loc", ns):
            urls.append(url_tag.text.strip())
    except ET.ParseError:
        pass
    return urls


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    return m.group(1).strip() if m else ""
