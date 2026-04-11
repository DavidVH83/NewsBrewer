"""Tests for the AnalystAgent class.

All tests mock the :class:`~src.providers.github_models.GitHubModelsProvider`
so that no real HTTP requests are made.  A real in-memory SQLite database is
used via :class:`~src.knowledge.database.KnowledgeDatabase` with the special
``":memory:"`` path so that schema creation works correctly without touching
the filesystem.

Run with::

    pytest tests/test_analyst_agent.py -v
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.agents.analyst_agent import AnalystAgent
from src.knowledge.database import KnowledgeDatabase
from src.models.article import Article
from src.models.digest_item import DigestItem


# ---------------------------------------------------------------------------
# Test config stub
# ---------------------------------------------------------------------------

def _make_config(min_relevance_score: float = 6.0) -> MagicMock:
    """Build a :class:`~unittest.mock.MagicMock` that mimics a Config object.

    Args:
        min_relevance_score: Minimum score threshold to use in tests.

    Returns:
        A MagicMock configured with the attributes accessed by
        :class:`~src.agents.analyst_agent.AnalystAgent`.
    """
    config = MagicMock()
    config.github_token = "fake-token"
    config.model.name = "gpt-4o-mini"
    config.ai_focus.language = "English"
    config.ai_focus.interesting_topics = ["LLM", "RAG", "AI agents"]
    config.ai_focus.not_interesting_topics = ["crypto", "NFTs"]
    config.ai_focus.min_relevance_score = min_relevance_score
    return config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db() -> KnowledgeDatabase:
    """Return an in-memory :class:`~src.knowledge.database.KnowledgeDatabase`.

    Returns:
        A fresh :class:`KnowledgeDatabase` backed by an in-memory SQLite
        database.  Each test gets its own instance because the fixture has
        function scope.
    """
    database = KnowledgeDatabase(db_path=":memory:")
    yield database
    database.close()


def _make_article(
    url: str = "https://example.com/article",
    title: str = "Test Article About LLMs",
    full_text: str = "This article is about large language models and their applications.",
    fetch_status: str = "success",
    source_domain: str = "example.com",
) -> Article:
    """Factory that creates an :class:`~src.models.article.Article` for tests.

    Args:
        url: Article URL.
        title: Article headline.
        full_text: Body text of the article.
        fetch_status: One of ``"success"``, ``"partial"``, or ``"failed"``.
        source_domain: Registered domain of the article URL.

    Returns:
        A populated :class:`Article` instance.
    """
    return Article(
        url=url,
        title=title,
        full_text=full_text,
        word_count=len(full_text.split()),
        fetch_status=fetch_status,
        source_domain=source_domain,
        fetched_at=datetime(2026, 4, 9, 12, 0, 0),
    )


def _ai_response(
    relevant: bool = True,
    relevance_score: float = 8.5,
    summary: str = "A great article about LLMs.",
    is_course: bool = False,
    tags: list[str] | None = None,
) -> str:
    """Return a JSON string mimicking a valid AI provider response.

    Args:
        relevant: Whether the article is relevant.
        relevance_score: Relevance score in the range 0.0–10.0.
        summary: Short summary text.
        is_course: Whether the item should be marked as a course.
        tags: List of tag strings; defaults to ``["LLM"]``.

    Returns:
        A JSON-encoded string matching the format expected by
        :meth:`~src.agents.analyst_agent.AnalystAgent._parse_ai_response`.
    """
    if tags is None:
        tags = ["LLM"]
    return json.dumps(
        {
            "relevant": relevant,
            "relevance_score": relevance_score,
            "summary": summary,
            "is_course": is_course,
            "tags": tags,
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@patch("src.agents.analyst_agent.GitHubModelsProvider")
def test_relevant_article_returns_digest_item(
    mock_provider_cls: MagicMock, db: KnowledgeDatabase
) -> None:
    """A relevant article with score 8.5 produces a DigestItem.

    When the AI returns relevant=true and relevance_score=8.5 (above the
    default threshold of 6.0), :meth:`~AnalystAgent.run` should return a
    list containing exactly one :class:`DigestItem` with the expected fields.
    """
    mock_provider_cls.return_value.complete.return_value = _ai_response(
        relevant=True, relevance_score=8.5, summary="Highly relevant LLM article."
    )

    agent = AnalystAgent(config=_make_config(), db=db)
    article = _make_article()

    items = agent.run([article])

    assert len(items) == 1
    item = items[0]
    assert isinstance(item, DigestItem)
    assert item.url == article.url
    assert item.title == article.title
    assert item.source == article.source_domain
    assert item.relevance_score == 8.5
    assert item.summary == "Highly relevant LLM article."


@patch("src.agents.analyst_agent.GitHubModelsProvider")
def test_irrelevant_article_returns_none(
    mock_provider_cls: MagicMock, db: KnowledgeDatabase
) -> None:
    """An article marked relevant=false is filtered out.

    When the AI returns relevant=false the agent must return an empty list
    and must not insert anything into the database.
    """
    mock_provider_cls.return_value.complete.return_value = _ai_response(
        relevant=False, relevance_score=3.0
    )

    agent = AnalystAgent(config=_make_config(), db=db)
    article = _make_article()

    items = agent.run([article])

    assert items == []
    assert db.already_seen(article.url) is False


@patch("src.agents.analyst_agent.GitHubModelsProvider")
def test_low_score_article_returns_none(
    mock_provider_cls: MagicMock, db: KnowledgeDatabase
) -> None:
    """An article with score 4.5 (below threshold 6.0) is filtered out.

    Even if relevant=true, a score below min_relevance_score must result in
    the agent returning an empty list.
    """
    mock_provider_cls.return_value.complete.return_value = _ai_response(
        relevant=True, relevance_score=4.5
    )

    agent = AnalystAgent(config=_make_config(min_relevance_score=6.0), db=db)
    article = _make_article()

    items = agent.run([article])

    assert items == []
    assert db.already_seen(article.url) is False


@patch("src.agents.analyst_agent.GitHubModelsProvider")
def test_course_detection(
    mock_provider_cls: MagicMock, db: KnowledgeDatabase
) -> None:
    """An article with is_course=true produces a DigestItem with is_course=True."""
    mock_provider_cls.return_value.complete.return_value = _ai_response(
        relevant=True,
        relevance_score=9.0,
        summary="Comprehensive LLM course.",
        is_course=True,
        tags=["LLM", "Course"],
    )

    agent = AnalystAgent(config=_make_config(), db=db)
    article = _make_article(
        url="https://example.com/course",
        title="Complete Guide to LLMs",
    )

    items = agent.run([article])

    assert len(items) == 1
    assert items[0].is_course is True
    assert "Course" in items[0].tags


@patch("src.agents.analyst_agent.GitHubModelsProvider")
def test_json_with_markdown_fences_parsed(
    mock_provider_cls: MagicMock, db: KnowledgeDatabase
) -> None:
    """A response wrapped in ```json ... ``` fences is still parsed correctly.

    The agent must strip Markdown code fences before attempting JSON parsing
    so that models that wrap their output in code blocks still work.
    """
    raw_json = _ai_response(relevant=True, relevance_score=7.5, summary="Good article.")
    response_with_fences = f"```json\n{raw_json}\n```"
    mock_provider_cls.return_value.complete.return_value = response_with_fences

    agent = AnalystAgent(config=_make_config(), db=db)
    article = _make_article()

    items = agent.run([article])

    assert len(items) == 1
    assert items[0].relevance_score == 7.5
    assert items[0].summary == "Good article."


@patch("src.agents.analyst_agent.GitHubModelsProvider")
def test_failed_article_skipped(
    mock_provider_cls: MagicMock, db: KnowledgeDatabase
) -> None:
    """An article with fetch_status='failed' is skipped without calling the AI.

    The provider's complete method must never be invoked for failed articles.
    """
    agent = AnalystAgent(config=_make_config(), db=db)
    article = _make_article(fetch_status="failed")

    items = agent.run([article])

    assert items == []
    mock_provider_cls.return_value.complete.assert_not_called()


@patch("src.agents.analyst_agent.GitHubModelsProvider")
def test_results_sorted_by_score(
    mock_provider_cls: MagicMock, db: KnowledgeDatabase
) -> None:
    """Results are sorted by relevance_score descending; sub-threshold items excluded.

    Three articles with AI scores 7.0, 9.0, and 5.5 are processed.  The item
    scoring 5.5 is below the default threshold of 6.0 and must be excluded.
    The remaining two must be returned in descending order: [9.0, 7.0].
    """
    scores = [7.0, 9.0, 5.5]
    responses = [
        _ai_response(relevant=True, relevance_score=s, summary=f"Article with score {s}.")
        for s in scores
    ]
    mock_provider_cls.return_value.complete.side_effect = responses

    agent = AnalystAgent(config=_make_config(min_relevance_score=6.0), db=db)
    articles = [
        _make_article(
            url=f"https://example.com/article-{i}",
            title=f"Article {i}",
        )
        for i in range(3)
    ]

    items = agent.run(articles)

    assert len(items) == 2
    assert items[0].relevance_score == 9.0
    assert items[1].relevance_score == 7.0
