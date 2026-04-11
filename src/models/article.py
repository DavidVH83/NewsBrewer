"""Data model for a fetched and parsed article."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Article:
    """Represents a web article fetched from a URL found in a newsletter.

    Attributes:
        url: The canonical URL of the article.
        title: The page title or article headline.
        full_text: Full extracted text content of the article body.
        word_count: Number of words in full_text.
        fetch_status: Outcome of the fetch attempt. One of:
            - "success"  — full content retrieved and parsed.
            - "partial"  — content retrieved but may be truncated or paywalled.
            - "failed"   — fetch or parse error; full_text may be empty.
        source_domain: Registered domain of the article URL (e.g. "medium.com").
        fetched_at: UTC datetime when the fetch was performed.
    """

    url: str
    title: str
    full_text: str
    word_count: int
    fetch_status: str  # "success" | "partial" | "failed"
    source_domain: str
    fetched_at: datetime
