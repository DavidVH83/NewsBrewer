"""Tests for the KnowledgeDatabase class.

All tests use pytest's ``tmp_path`` fixture so that each test receives its
own temporary SQLite database file — this guarantees full isolation without
any shared state between test functions.

Run with::

    pytest tests/test_knowledge_database.py -v
"""

from datetime import datetime, date

import pytest

from src.knowledge.database import KnowledgeDatabase
from src.models.digest_item import DigestItem


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: pytest.TempPathFactory) -> KnowledgeDatabase:
    """Return a fresh :class:`KnowledgeDatabase` backed by a temporary file.

    Args:
        tmp_path: Pytest-provided temporary directory unique to each test.

    Returns:
        An initialised :class:`KnowledgeDatabase` instance.
    """
    db_file = tmp_path / "test_knowledge.db"
    database = KnowledgeDatabase(db_path=str(db_file))
    yield database
    database.close()


def _make_item(
    url: str = "https://example.com/article",
    title: str = "Test Article",
    source: str = "example.com",
    summary: str = "A concise test summary about AI.",
    is_course: bool = False,
    relevance_score: float = 7.5,
    tags: list[str] | None = None,
    article_date: datetime | None = None,
) -> DigestItem:
    """Factory helper that builds a :class:`DigestItem` with sensible defaults.

    Args:
        url: Article URL (must be unique if inserting multiple items).
        title: Article title.
        source: Publication or domain name.
        summary: AI-generated summary text.
        is_course: Whether the item is a course/learning resource.
        relevance_score: Relevance score in the range 0.0–10.0.
        tags: List of tag strings; defaults to ``["AI", "Testing"]``.
        article_date: Publication date; defaults to today.

    Returns:
        A fully populated :class:`DigestItem`.
    """
    if tags is None:
        tags = ["AI", "Testing"]
    if article_date is None:
        article_date = datetime(2026, 4, 9, 12, 0, 0)
    return DigestItem(
        url=url,
        title=title,
        source=source,
        summary=summary,
        is_course=is_course,
        relevance_score=relevance_score,
        tags=tags,
        date=article_date,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_insert_and_retrieve_by_date(db: KnowledgeDatabase) -> None:
    """Inserted item is returned by get_by_date for its publication date."""
    item = _make_item(article_date=datetime(2026, 4, 9))
    db.insert(item)

    results = db.get_by_date(date(2026, 4, 9))

    assert len(results) == 1
    assert results[0]["url"] == item.url
    assert results[0]["title"] == item.title
    assert results[0]["source"] == item.source
    assert results[0]["summary"] == item.summary


def test_full_text_search(db: KnowledgeDatabase) -> None:
    """Inserting an item with a distinctive title/summary makes it findable via search."""
    item = _make_item(
        url="https://example.com/rag",
        title="Deep Dive into Retrieval Augmented Generation",
        summary="RAG combines dense retrieval with language model generation.",
        tags=["RAG", "LLM"],
    )
    db.insert(item)

    results = db.search("Retrieval Augmented Generation")

    assert len(results) >= 1
    urls = [r["url"] for r in results]
    assert item.url in urls


def test_already_seen_returns_true(db: KnowledgeDatabase) -> None:
    """already_seen returns True after the URL has been inserted."""
    item = _make_item(url="https://example.com/seen")
    db.insert(item)

    assert db.already_seen("https://example.com/seen") is True


def test_already_seen_returns_false(db: KnowledgeDatabase) -> None:
    """already_seen returns False for a URL that has never been inserted."""
    assert db.already_seen("https://example.com/never-seen") is False


def test_duplicate_url_skipped(db: KnowledgeDatabase) -> None:
    """Inserting the same URL twice does not raise and results in exactly one record."""
    item = _make_item(url="https://example.com/duplicate")
    db.insert(item)
    db.insert(item)  # Second insert should be silently ignored.

    results = db.get_by_date(date(2026, 4, 9))
    matching = [r for r in results if r["url"] == item.url]
    assert len(matching) == 1


def test_search_courses(db: KnowledgeDatabase) -> None:
    """get_courses returns only items where is_course is True."""
    course = _make_item(
        url="https://example.com/course",
        title="Intro to LLMs — Full Course",
        is_course=True,
        relevance_score=9.0,
        tags=["LLM", "Course"],
    )
    article = _make_item(
        url="https://example.com/article",
        title="LLM News Article",
        is_course=False,
        relevance_score=8.0,
        tags=["LLM"],
    )
    db.insert(course)
    db.insert(article)

    results = db.get_courses(limit=20)

    assert len(results) == 1
    assert results[0]["url"] == course.url
    assert results[0]["is_course"] == 1


def test_get_stats_empty_db(db: KnowledgeDatabase) -> None:
    """get_stats on an empty database returns zeroed-out values and None date_range."""
    stats = db.get_stats()

    assert stats["total_articles"] == 0
    assert stats["date_range"] is None
    assert stats["course_count"] == 0
    assert stats["top_tags"] == []


def test_get_stats_with_data(db: KnowledgeDatabase) -> None:
    """get_stats returns accurate counts after inserting a mix of items."""
    items = [
        _make_item(
            url="https://example.com/a1",
            tags=["RAG", "LLM"],
            is_course=False,
            article_date=datetime(2026, 1, 1),
        ),
        _make_item(
            url="https://example.com/a2",
            tags=["RAG", "Agents"],
            is_course=True,
            article_date=datetime(2026, 4, 9),
        ),
        _make_item(
            url="https://example.com/a3",
            tags=["LLM"],
            is_course=False,
            article_date=datetime(2026, 3, 15),
        ),
    ]
    for item in items:
        db.insert(item)

    stats = db.get_stats()

    assert stats["total_articles"] == 3
    assert stats["course_count"] == 1
    assert stats["date_range"] is not None
    assert stats["date_range"]["from"] == "2026-01-01"
    assert stats["date_range"]["to"] == "2026-04-09"

    # RAG appears twice and LLM appears twice; both should be in top_tags.
    tag_names = [t["tag"] for t in stats["top_tags"]]
    assert "RAG" in tag_names
    assert "LLM" in tag_names

    # Counts should be correct.
    rag_entry = next(t for t in stats["top_tags"] if t["tag"] == "RAG")
    assert rag_entry["count"] == 2
