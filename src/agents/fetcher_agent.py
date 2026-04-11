"""Async article fetching agent for the NewsBrewer pipeline.

Provides :class:`FetcherAgent`, which concurrently downloads a list of URLs,
strips HTML noise, and returns a list of :class:`~src.models.article.Article`
objects.  Errors are always caught and encoded in the returned Article's
``fetch_status`` field — this module never raises exceptions to callers.
"""

import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from src.models.article import Article
from src.utils.logger import get_logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NOISE_TAGS: tuple[str, ...] = (
    "nav", "footer", "header", "script", "style", "aside",
)

# Substrings that, when found in an element's ``class`` or ``id`` attribute,
# mark it as noise to be removed before text extraction.
_NOISE_ATTR_PATTERNS: tuple[str, ...] = (
    "ad", "advertisement", "sidebar", "cookie", "popup",
    "banner", "nav", "menu",
)

_DEFAULT_MAX_CONCURRENT = 5
_DEFAULT_TIMEOUT = 15          # seconds
_DEFAULT_MAX_WORDS = 4000

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _attr_contains_noise(value: str | list[str] | None) -> bool:
    """Return True if a class or id attribute value contains a noise keyword.

    Args:
        value: The raw attribute value from BeautifulSoup, which may be a
            string, a list of strings (as BS4 returns for ``class``), or
            ``None`` when the attribute is absent.

    Returns:
        ``True`` when any noise pattern appears (case-insensitive) inside
        the attribute value; ``False`` otherwise or when *value* is ``None``.
    """
    if value is None:
        return False
    if isinstance(value, list):
        combined = " ".join(v for v in value if v).lower()
    else:
        combined = value.lower()
    return any(pattern in combined for pattern in _NOISE_ATTR_PATTERNS)


# ---------------------------------------------------------------------------
# Medium RSS helpers
# ---------------------------------------------------------------------------

def _is_medium_url(url: str) -> bool:
    """Return True if this is a medium.com article URL."""
    parsed = urlparse(url)
    return "medium.com" in parsed.netloc


def _medium_rss_url(article_url: str) -> str | None:
    """Derive the Medium RSS feed URL from an article URL.

    Examples:
      https://medium.com/@username/article-slug → https://medium.com/feed/@username
      https://medium.com/publication/article-slug → https://medium.com/feed/publication
    """
    try:
        parsed = urlparse(article_url)
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        if not parts:
            return None
        # First segment is either @username or publication-name
        feed_path = parts[0]  # e.g. "@username" or "towards-data-science"
        return f"https://medium.com/feed/{feed_path}"
    except Exception:
        return None


async def _fetch_medium_via_rss(
    client: httpx.AsyncClient,
    url: str,
    logger,
) -> tuple[str, str] | None:
    """Try to get article title and text from Medium's RSS feed.

    Returns (title, full_text) or None if not found in feed.
    """
    rss_url = _medium_rss_url(url)
    if not rss_url:
        return None

    try:
        response = await client.get(rss_url, timeout=15, follow_redirects=True)
        if response.status_code != 200:
            return None

        # Parse RSS/Atom XML
        root = ET.fromstring(response.text)

        # Handle both RSS and Atom namespaces
        ns = {"content": "http://purl.org/rss/1.0/modules/content/"}

        # Find items (RSS) or entries (Atom)
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")

        for item in items:
            # Get item link
            link_el = item.find("link")
            link = link_el.text if link_el is not None else ""

            # Check if this item matches our URL (compare slug)
            article_slug = url.split("/")[-1].split("?")[0]
            if article_slug and article_slug not in (link or ""):
                continue

            # Get title
            title_el = item.find("title")
            title = title_el.text if title_el is not None else "Unknown"

            # Get full content (content:encoded has the full HTML)
            content_el = item.find("content:encoded", ns)
            if content_el is None or not content_el.text:
                # Fallback to description
                content_el = item.find("description")

            if content_el is not None and content_el.text:
                # Parse HTML content
                soup = BeautifulSoup(content_el.text, "lxml")
                text = soup.get_text(separator=" ", strip=True)
                text = " ".join(text.split())
                words = text.split()[:4000]
                return title, " ".join(words)

        return None

    except Exception as e:
        logger.debug("RSS fetch failed for %s: %s", rss_url, e)
        return None


# ---------------------------------------------------------------------------
# FetcherAgent
# ---------------------------------------------------------------------------

class FetcherAgent:
    """Async agent that fetches and parses article HTML from a list of URLs.

    Attributes:
        logger: Module-level logger returned by :func:`~src.utils.logger.get_logger`.
        max_concurrent: Maximum number of simultaneous HTTP connections (default 5).
        timeout: Per-request timeout in seconds (default 15).
        max_words: Maximum number of words kept in ``full_text`` (default 4000).
        headers: HTTP request headers sent with every request.
    """

    def __init__(self, config: dict | None = None) -> None:
        """Initialise the FetcherAgent.

        Args:
            config: Optional configuration dictionary (reserved for future use;
                currently ignored — all settings use hardcoded defaults).
        """
        self.logger = get_logger(__name__)
        self.max_concurrent: int = _DEFAULT_MAX_CONCURRENT
        self.timeout: int = _DEFAULT_TIMEOUT
        self.max_words: int = _DEFAULT_MAX_WORDS
        self.headers: dict[str, str] = {"User-Agent": _USER_AGENT}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_content(self, soup: BeautifulSoup) -> str:
        """Strip noise elements and extract the main article text.

        Noise removal steps (applied in order):
        1. Decompose tags whose *name* is in :data:`_NOISE_TAGS`.
        2. Decompose any remaining element whose ``class`` or ``id`` attribute
           contains a noise keyword from :data:`_NOISE_ATTR_PATTERNS`.

        Content extraction priority:
        ``<article>`` → ``<main>`` → ``class='content'`` → ``<body>``

        The extracted text is whitespace-normalised and truncated to
        :attr:`max_words` words.

        Args:
            soup: A parsed :class:`BeautifulSoup` document.

        Returns:
            Cleaned, normalised, and truncated article text.
        """
        # Step 1 — remove noisy structural tags.
        for tag_name in _NOISE_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # Step 2 — remove elements with noisy class / id values.
        # Guard against Tags whose .attrs dict is None (can occur with lxml
        # after sibling decompose operations).
        for element in soup.find_all(True):  # True = any tag
            if not element.attrs:
                continue
            cls = element.get("class", [])
            eid = element.get("id", "")
            if _attr_contains_noise(cls) or _attr_contains_noise(eid):
                element.decompose()

        # Step 3 — locate the best content container.
        content_node = (
            soup.find("article")
            or soup.find("main")
            or soup.find(class_="content")
            or soup.find("body")
            or soup
        )

        raw_text: str = content_node.get_text(separator=" ", strip=True)  # type: ignore[union-attr]
        normalised: str = " ".join(raw_text.split())

        # Step 4 — truncate to max_words.
        words = normalised.split()
        if len(words) > self.max_words:
            normalised = " ".join(words[: self.max_words])

        return normalised

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_one(self, client: httpx.AsyncClient, url: str) -> Article:
        """Fetch and parse a single article URL.

        This method never raises an exception.  All error conditions are
        captured and encoded in the returned :class:`~src.models.article.Article`
        via its ``fetch_status`` field.

        Behaviour on specific error conditions:

        * ``httpx.TimeoutException`` → ``fetch_status="failed"``, empty text,
          ``word_count=0``.
        * HTTP 401, 403, 429 → ``fetch_status="partial"``; whatever content
          the server returned is still parsed and returned.
        * Any other exception → ``fetch_status="failed"``, empty text.

        Args:
            client: A shared :class:`httpx.AsyncClient` instance to use for
                the request.  Callers are responsible for the client lifecycle.
            url: The fully-qualified HTTP/HTTPS URL to fetch.

        Returns:
            An :class:`~src.models.article.Article` dataclass instance
            populated with the parsed content and an appropriate
            ``fetch_status``.
        """
        self.logger.info("Fetching URL: %s", url)
        source_domain = urlparse(url).netloc

        # Try Medium RSS first for medium.com URLs
        if _is_medium_url(url):
            rss_result = await _fetch_medium_via_rss(client, url, self.logger)
            if rss_result:
                title, full_text = rss_result
                words = full_text.split()
                self.logger.info(
                    "Fetched via RSS '%s' (%d words, status=success) from %s",
                    title, len(words), url
                )
                return Article(
                    url=url,
                    title=title,
                    full_text=full_text,
                    word_count=len(words),
                    fetch_status="success",
                    source_domain=urlparse(url).netloc,
                    fetched_at=datetime.now(tz=timezone.utc),
                )
            # RSS failed — fall through to normal fetch (will likely get 403)
            self.logger.debug("RSS fallback failed for %s, trying direct fetch", url)

        try:
            response = await client.get(
                url,
                timeout=self.timeout,
                follow_redirects=True,
                headers=self.headers,
            )
            self.logger.debug(
                "Received HTTP %s for %s", response.status_code, url
            )

            # Determine fetch_status based on HTTP status code.
            if response.status_code in (401, 403, 429):
                self.logger.warning(
                    "HTTP %s (restricted) for %s — marking partial",
                    response.status_code,
                    url,
                )
                fetch_status = "partial"
            else:
                response.raise_for_status()
                fetch_status = "success"

            # Parse HTML — prefer lxml for speed; fall back to html.parser.
            try:
                soup = BeautifulSoup(response.text, "lxml")
            except Exception:  # noqa: BLE001
                soup = BeautifulSoup(response.text, "html.parser")

            # Extract title.
            title: str = "Unknown"
            if soup.title and soup.title.string:
                title = soup.title.string.strip()

            # Extract and clean body text.
            full_text = self._parse_content(soup)
            word_count = len(full_text.split()) if full_text else 0

            self.logger.info(
                "Fetched '%s' (%d words, status=%s) from %s",
                title,
                word_count,
                fetch_status,
                url,
            )

            return Article(
                url=url,
                title=title,
                full_text=full_text,
                word_count=word_count,
                fetch_status=fetch_status,
                source_domain=source_domain,
                fetched_at=datetime.now(tz=timezone.utc),
            )

        except httpx.TimeoutException as exc:
            self.logger.error("Timeout fetching %s: %s", url, exc)
            return Article(
                url=url,
                title="Unknown",
                full_text="",
                word_count=0,
                fetch_status="failed",
                source_domain=source_domain,
                fetched_at=datetime.now(tz=timezone.utc),
            )

        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Unexpected error fetching %s: %s: %s",
                url,
                type(exc).__name__,
                exc,
            )
            return Article(
                url=url,
                title="Unknown",
                full_text="",
                word_count=0,
                fetch_status="failed",
                source_domain=source_domain,
                fetched_at=datetime.now(tz=timezone.utc),
            )

    async def run(self, urls: list[str]) -> list[Article]:
        """Fetch all URLs concurrently, returning one Article per URL.

        Concurrency is bounded by a :class:`asyncio.Semaphore` capped at
        :attr:`max_concurrent` (default 5).  A single
        :class:`httpx.AsyncClient` is shared across all requests to enable
        connection reuse.

        None results are filtered out, though in practice :meth:`fetch_one`
        always returns an :class:`~src.models.article.Article` — so filtering
        is a safety net only.

        Args:
            urls: List of HTTP/HTTPS URL strings to fetch.

        Returns:
            A list of :class:`~src.models.article.Article` objects in the
            same order as the input ``urls``, minus any (unexpected) ``None``
            values.
        """
        self.logger.info("Starting fetch run for %d URL(s)", len(urls))
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def _bounded_fetch(
            client: httpx.AsyncClient, url: str
        ) -> Article:
            """Wrap fetch_one with semaphore-based concurrency control.

            Args:
                client: Shared HTTP client.
                url: URL to fetch.

            Returns:
                The :class:`~src.models.article.Article` returned by
                :meth:`fetch_one`.
            """
            async with semaphore:
                return await self.fetch_one(client, url)

        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(
                *[_bounded_fetch(client, url) for url in urls],
                return_exceptions=False,
            )

        articles: list[Article] = [r for r in results if r is not None]
        self.logger.info(
            "Fetch run complete: %d/%d articles returned",
            len(articles),
            len(urls),
        )
        return articles
