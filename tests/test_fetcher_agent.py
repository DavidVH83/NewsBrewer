"""Tests for FetcherAgent — async article fetching and HTML parsing.

Uses ``respx`` to mock all HTTP traffic so no real network calls are made.
Run with::

    pytest tests/test_fetcher_agent.py -v
"""

import asyncio
import pathlib

import httpx
import pytest
import respx

from src.agents.fetcher_agent import FetcherAgent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _load_fixture(filename: str) -> str:
    """Return the text content of a file in the fixtures directory.

    Args:
        filename: Bare filename (e.g. ``"sample_article.html"``).

    Returns:
        File contents as a UTF-8 string.
    """
    return (_FIXTURES / filename).read_text(encoding="utf-8")


def _simple_html(body: str, title: str = "Test Title") -> str:
    """Wrap *body* HTML in a minimal complete HTML document.

    Args:
        body: Inner HTML to place inside ``<body>``.
        title: Value for the ``<title>`` tag.

    Returns:
        A complete HTML string.
    """
    return (
        f"<html><head><title>{title}</title></head>"
        f"<body>{body}</body></html>"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_one_success() -> None:
    """Successful 200 response returns an Article with fetch_status='success'.

    Verifies that:
    - ``fetch_status`` is ``"success"``
    - ``title`` is extracted from the ``<title>`` tag
    - ``word_count`` matches the number of words in ``full_text``
    - ``source_domain`` is derived from the URL
    - ``url`` is preserved exactly
    """
    url = "https://example.com/article"
    html = _load_fixture("sample_article.html")
    respx.get(url).mock(return_value=httpx.Response(200, text=html))

    agent = FetcherAgent()
    async with httpx.AsyncClient() as client:
        article = await agent.fetch_one(client, url)

    assert article.fetch_status == "success"
    assert article.title == "Understanding RAG: A Complete Guide for Developers"
    assert article.url == url
    assert article.source_domain == "example.com"
    assert article.word_count == len(article.full_text.split())
    assert article.word_count > 0
    assert article.fetched_at is not None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_one_timeout() -> None:
    """A TimeoutException produces an Article with fetch_status='failed'.

    Verifies that:
    - ``fetch_status`` is ``"failed"``
    - ``full_text`` is an empty string
    - ``word_count`` is 0
    - The method does not raise
    """
    url = "https://example.com/slow-article"
    respx.get(url).mock(side_effect=httpx.TimeoutException("timed out"))

    agent = FetcherAgent()
    async with httpx.AsyncClient() as client:
        article = await agent.fetch_one(client, url)

    assert article.fetch_status == "failed"
    assert article.full_text == ""
    assert article.word_count == 0
    assert article.url == url


@pytest.mark.asyncio
@respx.mock
async def test_fetch_one_403_paywall() -> None:
    """A 403 response returns an Article with fetch_status='partial'.

    The 403 status typically indicates a paywall or access restriction.
    Whatever body the server returned is still parsed and included.

    Verifies that:
    - ``fetch_status`` is ``"partial"``
    - The method does not raise
    """
    url = "https://example.com/paywalled"
    html = _simple_html(
        "<article><p>Preview text only. Subscribe to read more.</p></article>",
        title="Paywalled Article",
    )
    respx.get(url).mock(return_value=httpx.Response(403, text=html))

    agent = FetcherAgent()
    async with httpx.AsyncClient() as client:
        article = await agent.fetch_one(client, url)

    assert article.fetch_status == "partial"
    assert article.url == url


@pytest.mark.asyncio
@respx.mock
async def test_noise_removal() -> None:
    """Navigation, footer, header, and script tags are stripped from content.

    Verifies that noise keywords (e.g. "NAV_LINK", "FOOTER_TEXT",
    "SCRIPT_VAR") do not appear in the extracted ``full_text``, while the
    meaningful article body text ("ARTICLE_CONTENT") is preserved.
    """
    url = "https://example.com/noisy"
    html = (
        "<html><head><title>Noise Test</title>"
        "<script>var SCRIPT_VAR = 1;</script>"
        "<style>.nav { color: red; } /* STYLE_RULE */</style>"
        "</head><body>"
        "<header>NAV_LINK Home About</header>"
        "<nav>NAV_LINK Menu</nav>"
        "<aside class='sidebar'>SIDEBAR_TEXT</aside>"
        "<div class='advertisement'>AD_TEXT</div>"
        "<div id='cookie-banner'>COOKIE_TEXT</div>"
        "<article><p>ARTICLE_CONTENT This is the real body text.</p></article>"
        "<footer>FOOTER_TEXT Copyright 2026</footer>"
        "</body></html>"
    )
    respx.get(url).mock(return_value=httpx.Response(200, text=html))

    agent = FetcherAgent()
    async with httpx.AsyncClient() as client:
        article = await agent.fetch_one(client, url)

    assert "ARTICLE_CONTENT" in article.full_text
    assert "NAV_LINK" not in article.full_text
    assert "FOOTER_TEXT" not in article.full_text
    assert "SCRIPT_VAR" not in article.full_text
    assert "SIDEBAR_TEXT" not in article.full_text
    assert "AD_TEXT" not in article.full_text
    assert "COOKIE_TEXT" not in article.full_text


@pytest.mark.asyncio
@respx.mock
async def test_word_truncation() -> None:
    """Content exceeding 4000 words is truncated to exactly 4000 words.

    Verifies that:
    - ``word_count`` is exactly ``agent.max_words`` (4000)
    - ``full_text`` contains exactly 4000 space-separated tokens
    """
    url = "https://example.com/long-article"
    # Generate 5000 distinct words inside an <article> tag.
    long_body = " ".join(f"word{i}" for i in range(5000))
    html = _simple_html(f"<article><p>{long_body}</p></article>")
    respx.get(url).mock(return_value=httpx.Response(200, text=html))

    agent = FetcherAgent()
    async with httpx.AsyncClient() as client:
        article = await agent.fetch_one(client, url)

    assert article.word_count == agent.max_words
    assert len(article.full_text.split()) == agent.max_words


@pytest.mark.asyncio
@respx.mock
async def test_run_parallel() -> None:
    """run() fetches multiple URLs concurrently and returns one Article each.

    Five URLs are registered with respx mock routes.  The method must return
    exactly five Articles, all with fetch_status='success', in any order
    (but the URL set must match).

    Verifies that:
    - Exactly 5 articles are returned
    - Every input URL is represented in the output
    - All articles have ``fetch_status='success'``
    """
    base = "https://example.com/article"
    urls = [f"{base}/{i}" for i in range(5)]

    for i, url in enumerate(urls):
        html = _simple_html(
            f"<article><p>Content for article {i}.</p></article>",
            title=f"Article {i}",
        )
        respx.get(url).mock(return_value=httpx.Response(200, text=html))

    agent = FetcherAgent()
    articles = await agent.run(urls)

    assert len(articles) == 5
    returned_urls = {a.url for a in articles}
    assert returned_urls == set(urls)
    for article in articles:
        assert article.fetch_status == "success"
