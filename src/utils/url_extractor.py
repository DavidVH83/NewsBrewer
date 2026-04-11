"""URL extraction and filtering utilities for the NewsBrewer pipeline.

Extracts HTTP/HTTPS URLs from plain text and HTML email bodies, then applies
a multi-layer filter to remove tracking pixels, image assets, unsubscribe
links, and known tracking domains.  Duplicate URLs are removed while
preserving the order in which they first appear.

Typical usage::

    from src.utils.url_extractor import extract_urls

    urls = extract_urls(text=email.body_text, html=email.body_html)

Public API
----------
- :func:`extract_from_text`  — extract URLs from plain text.
- :func:`extract_from_html`  — extract URLs from HTML via BeautifulSoup.
- :func:`extract_urls`       — convenience wrapper that merges both sources.
"""

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants — filtering rules
# ---------------------------------------------------------------------------

# File extensions that indicate binary/image assets rather than articles.
_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp", ".tiff", ".tif"}
)

# Domains that are non-content (social media, app stores, etc.) — skip entirely.
_NON_CONTENT_DOMAINS: frozenset[str] = frozenset(
    {
        "facebook.com",
        "twitter.com",
        "x.com",
        "instagram.com",
        "tiktok.com",
        "linkedin.com",
        "youtube.com",
        "play.google.com",
        "apps.apple.com",
        "catawiki.com",
        "mailing.catawiki.com",
        "trustpilot.com",
        "policy.medium.com",
    }
)

# Medium-specific: path patterns that are NOT articles (profiles, publications, settings).
_MEDIUM_NON_ARTICLE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"^/@[\w.]+/?$"),               # /@username
    re.compile(r"^/me/"),                       # /me/settings etc.
    re.compile(r"^/jobs-at-medium/"),           # jobs
    re.compile(r"^/plans"),                     # plans/pricing
    re.compile(r"^/?$"),                        # homepage
)

# Domains (or domain substrings) known to be pure tracking infrastructure.
# Matching is done on the registered domain portion of the URL.
_TRACKING_DOMAINS: frozenset[str] = frozenset(
    {
        "list-manage.com",
        "mailchimp.com",
        "mc.sendgrid.net",
        "sendgrid.net",
        "click.convertkit-mail.com",
        "convertkit-mail.com",
        "click.mailerlite.com",
        "mailerlite.com",
        "tracking.tldrnewsletter.com",
        "tldrnewsletter.com",
        "link.mail.beehiiv.com",
        "beehiiv.com",
        "click.e.hubspot.com",
        "t.hubspotemail.net",
        "hubspotemail.net",
        "em.mimecast.com",
        "bounce.beefree.io",
        "mailtrack.io",
        "trk.email",
        "click.pstmrk.it",
        "postmarkapp.com",
        "r.email.substack.com",
        "substack-post-media.s3.amazonaws.com",
        "go.pardot.com",
        "click.e.salesforce.com",
        "bounce.actionmailbox.org",
    }
)

# Path fragments that strongly indicate tracking or administrative pages.
_TRACKING_PATH_FRAGMENTS: tuple[str, ...] = (
    "/track/",
    "/open/",
    "/click/",
    "/pixel/",
    "/unsubscribe",
    "/optout",
    "/opt-out",
    "/email-preferences",
    "/manage-preferences",
    "/manage_preferences",
    "/webversion",
    "/web-version",
    "/view-in-browser",
    "/view_online",
    "/mirror",
    "/forward",
    "/share-email",
)

# Regex for finding raw URLs in plain text.
_URL_RE = re.compile(
    r"https?://"                 # scheme
    r"[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+"  # path/query chars
    r"[A-Za-z0-9/]",            # must not end with punctuation
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_tracking_domain(hostname: str) -> bool:
    """Return True if *hostname* belongs to a known tracking domain.

    Checks whether the hostname ends with any entry in :data:`_TRACKING_DOMAINS`.
    A ``pixel.`` subdomain prefix is also treated as a tracking indicator.

    Args:
        hostname: The ``netloc`` component of a parsed URL (lowercased).

    Returns:
        True when the hostname matches a tracking domain pattern.
    """
    hostname_lower = hostname.lower().lstrip("www.")
    if hostname_lower.startswith("pixel."):
        return True
    for domain in _TRACKING_DOMAINS:
        if hostname_lower == domain or hostname_lower.endswith("." + domain):
            return True
    return False


def _has_image_extension(path: str) -> bool:
    """Return True if the URL path ends with a known image file extension.

    Args:
        path: The path component of a parsed URL.

    Returns:
        True when the path suggests a static image asset.
    """
    # Strip query string fragments before checking extension.
    base = path.split("?")[0].split("#")[0].lower()
    _, ext = _splitext_lower(base)
    return ext in _IMAGE_EXTENSIONS


def _splitext_lower(path: str) -> tuple[str, str]:
    """Return (root, ext) like :func:`os.path.splitext` but lowercased ext.

    Args:
        path: File path string to split.

    Returns:
        Tuple of (root, lowercased extension including the leading dot, or '').
    """
    dot_idx = path.rfind(".")
    slash_idx = path.rfind("/")
    if dot_idx > slash_idx and dot_idx != -1:
        return path[:dot_idx], path[dot_idx:].lower()
    return path, ""


def _has_tracking_path(path: str) -> bool:
    """Return True if the URL path contains a known tracking fragment.

    Args:
        path: The path component of a parsed URL (lowercased).

    Returns:
        True when the path matches a tracking or administrative pattern.
    """
    path_lower = path.lower()
    return any(frag in path_lower for frag in _TRACKING_PATH_FRAGMENTS)


def _is_filtered(url: str) -> bool:
    """Determine whether a URL should be excluded from extraction results.

    A URL is filtered out if it:
    - Cannot be parsed as a valid HTTP/HTTPS URL.
    - Belongs to a known tracking or non-content domain.
    - Has a path indicating a tracking pixel, unsubscribe page, or image asset.
    - Is a Medium profile/publication/settings page (not an article).

    Args:
        url: The raw URL string to evaluate.

    Returns:
        True when the URL should be excluded; False when it should be kept.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return True

    if parsed.scheme not in ("http", "https"):
        return True

    if not parsed.netloc:
        return True

    hostname = parsed.netloc.lower().lstrip("www.")

    if _is_tracking_domain(hostname):
        return True

    # Skip non-content domains entirely.
    for domain in _NON_CONTENT_DOMAINS:
        if hostname == domain or hostname.endswith("." + domain):
            return True

    if _has_image_extension(parsed.path):
        return True

    if _has_tracking_path(parsed.path):
        return True

    # Medium-specific: skip non-article paths.
    if "medium.com" in hostname:
        path = parsed.path
        for pattern in _MEDIUM_NON_ARTICLE_PATTERNS:
            if pattern.match(path):
                return True
        # Publication homepages: /publication-name with no article slug
        # Real articles have a long slug ending in a hex ID
        parts = [p for p in path.split("/") if p and not p.startswith("@")]
        if len(parts) == 1 and not re.search(r"-[0-9a-f]{8,}$", parts[0]):
            return True  # looks like a publication homepage, not an article

    return False


def _deduplicate(urls: list[str]) -> list[str]:
    """Remove duplicate URLs while preserving first-occurrence order.

    Args:
        urls: List of URL strings that may contain duplicates.

    Returns:
        New list with duplicates removed, order preserved.
    """
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_from_text(text: str) -> list[str]:
    """Extract and filter HTTP/HTTPS URLs from a plain-text string.

    Uses a regex to find all URL-like substrings, then applies domain and
    path-based filters to remove tracking and image links.

    Args:
        text: Raw plain-text content (e.g. the text/plain MIME part of an
            email).  May be an empty string.

    Returns:
        Deduplicated list of URLs that passed all filters, in the order they
        first appeared in *text*.

    Example::

        urls = extract_from_text("Check out https://example.com/article for more.")
        # -> ["https://example.com/article"]
    """
    if not text:
        return []

    raw_urls = _URL_RE.findall(text)
    filtered = [url for url in raw_urls if not _is_filtered(url)]
    result = _deduplicate(filtered)
    logger.debug("extract_from_text: found %d raw, kept %d", len(raw_urls), len(result))
    return result


def extract_from_html(html: str) -> list[str]:
    """Extract and filter HTTP/HTTPS URLs from an HTML string.

    Parses the HTML with BeautifulSoup/lxml and collects ``href`` attributes
    from ``<a>`` tags, then applies the same domain and path filters as
    :func:`extract_from_text`.

    Args:
        html: Raw HTML content (e.g. the text/html MIME part of an email).
            May be an empty string.

    Returns:
        Deduplicated list of URLs that passed all filters, in the order they
        first appeared in the HTML source.

    Example::

        urls = extract_from_html('<a href="https://example.com/post">Read</a>')
        # -> ["https://example.com/post"]
    """
    if not html:
        return []

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        # Fall back to the pure-Python parser if lxml is unavailable.
        soup = BeautifulSoup(html, "html.parser")

    raw_urls: list[str] = []
    for tag in soup.find_all("a", href=True):
        href: str = tag["href"].strip()
        if href.startswith(("http://", "https://")):
            raw_urls.append(href)

    filtered = [url for url in raw_urls if not _is_filtered(url)]
    result = _deduplicate(filtered)
    logger.debug(
        "extract_from_html: found %d <a href> links, kept %d",
        len(raw_urls),
        len(result),
    )
    return result


def extract_urls(text: str, html: str) -> list[str]:
    """Extract, filter, and merge URLs from both plain text and HTML sources.

    Combines results from :func:`extract_from_html` (higher fidelity — actual
    links) and :func:`extract_from_text` (catches URLs in plain-text parts
    not represented as hyperlinks), then deduplicates the merged list.

    HTML-sourced URLs are processed first and take positional priority.

    Args:
        text: Plain-text body of the email (may be empty string).
        html: HTML body of the email (may be empty string).

    Returns:
        Deduplicated list of article-quality URLs, in the order they first
        appeared across both sources (HTML results first).

    Example::

        urls = extract_urls(text=email.body_text, html=email.body_html)
        for url in urls:
            print(url)
    """
    html_urls = extract_from_html(html)
    text_urls = extract_from_text(text)

    # Merge: HTML first, then any extra URLs found only in the text part.
    merged = _deduplicate(html_urls + text_urls)
    logger.info(
        "extract_urls: %d from HTML + %d from text -> %d unique after dedup",
        len(html_urls),
        len(text_urls),
        len(merged),
    )
    return merged
