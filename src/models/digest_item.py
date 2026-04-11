"""Data model for a single item included in a NewsBrewer digest."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DigestItem:
    """Represents one article or resource selected for inclusion in a digest.

    DigestItems are produced by the summarisation/ranking agent after it
    evaluates fetched articles against the user's configured AI focus areas.

    Attributes:
        url: The source URL of the article.
        title: Human-readable title of the article or resource.
        source: Domain or publication name (e.g. "The Batch", "Towards AI").
        summary: AI-generated concise summary (2-4 sentences) of the article.
        is_course: True when the item is an online course or learning resource,
            used to apply the highlight_courses visual treatment in the digest.
        relevance_score: Float in the range 0.0–10.0 indicating how relevant
            this article is to the user's configured interesting_topics.
        tags: Short descriptive tags derived from the article content, used
            for categorisation in the digest email template.
        date: Publication date of the article, or date it was fetched if the
            original publication date could not be determined.
    """

    url: str
    title: str
    source: str
    summary: str
    is_course: bool
    relevance_score: float  # 0.0 - 10.0
    tags: list[str]
    date: datetime
