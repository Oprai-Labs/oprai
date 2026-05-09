"""Base source adapter abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator, Literal, Optional


@dataclass
class SourceConfig:
    id: str
    source_type: Literal["docs", "sitemap", "rss", "github", "cookbook", "defillama", "governance"]
    base_url: str
    protocol: Optional[str]
    category: str
    license: str
    schedule_cron: str
    language: str = "en"
    crawl_delay_s: float = 1.0
    max_pages: int = 5000
    include_patterns: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)
    legal_review_passed: bool = False
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentRef:
    doc_id: str
    url: str
    last_modified: Optional[datetime] = None
    etag: Optional[str] = None


@dataclass
class RawDocument:
    ref: DocumentRef
    content_type: Literal["html", "markdown", "rss_item", "json", "code"]
    body: str
    title: str
    section_path: str = ""
    published_at: Optional[datetime] = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)


class Source(ABC):
    """Abstract source adapter — one per content type/site family."""

    @abstractmethod
    async def discover(self, cfg: SourceConfig) -> AsyncIterator[DocumentRef]:
        """Yield all document references for a source config."""
        ...

    @abstractmethod
    async def fetch(self, ref: DocumentRef, cfg: SourceConfig) -> Optional[RawDocument]:
        """Fetch and return a raw document. Returns None if skipped/failed."""
        ...
