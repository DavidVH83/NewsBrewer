"""Tests for DigestAgent and the html_builder utility.

All tests use mocks — no real SMTP connections or file-system template lookups
are performed beyond what Jinja2 needs for the actual template file.

Run with::

    pytest tests/test_digest_agent.py -v
"""

import smtplib
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.agents.digest_agent import DigestAgent
from src.models.digest_item import DigestItem
from src.utils.config_loader import (
    AccountConfig,
    AIFocusConfig,
    Config,
    DeliveryConfig,
    ModelConfig,
)
from src.utils.html_builder import build_digest_html


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def minimal_config() -> Config:
    """Return a minimal Config with dummy SMTP credentials for testing.

    No real connections are made; credentials are placeholders only.
    """
    account = AccountConfig(
        name="Test Gmail",
        imap_server="imap.gmail.com",
        imap_port=993,
        email="test@gmail.com",
        app_password="dummy-app-password",
    )
    return Config(
        email_sources=[account],
        newsletter_senders=["noreply@medium.com"],
        manual_keyword="BREW",
        ai_focus=AIFocusConfig(
            language="en",
            interesting_topics=["AI agents"],
            not_interesting_topics=[],
            min_relevance_score=6.0,
            highlight_courses=True,
        ),
        delivery=DeliveryConfig(
            smtp_server="smtp.gmail.com",
            smtp_port=587,
            smtp_email="sender@gmail.com",
            smtp_password="smtp-dummy",
            send_to="recipient@example.com",
            max_articles=10,
            schedule="0 6 * * *",
            timezone="UTC",
        ),
        model=ModelConfig(provider="github_models", name="gpt-4o-mini"),
        github_token="dummy-token",
    )


def _make_item(
    title: str = "Test Article",
    url: str = "https://example.com/article",
    source: str = "example.com",
    summary: str = "A short summary of the article.",
    is_course: bool = False,
    relevance_score: float = 8.5,
    tags: list[str] | None = None,
) -> DigestItem:
    """Helper factory for DigestItem test instances.

    Args:
        title: Article title.
        url: Source URL.
        source: Domain or publication name.
        summary: AI-generated summary text.
        is_course: Whether this item is a course / learning resource.
        relevance_score: Relevance score between 0.0 and 10.0.
        tags: Optional list of tag strings; defaults to ``["AI", "testing"]``.

    Returns:
        A populated :class:`~src.models.digest_item.DigestItem`.
    """
    if tags is None:
        tags = ["AI", "testing"]
    return DigestItem(
        url=url,
        title=title,
        source=source,
        summary=summary,
        is_course=is_course,
        relevance_score=relevance_score,
        tags=tags,
        date=datetime(2026, 4, 9, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# 1. test_build_html_contains_title
# ---------------------------------------------------------------------------

def test_build_html_contains_title() -> None:
    """Rendered HTML contains the DigestItem's title as visible text.

    Passes a single non-course item to :func:`build_digest_html` and asserts
    that the item's title string appears somewhere in the output.
    """
    item = _make_item(title="Understanding LLM Agents in 2026")
    html = build_digest_html([item], digest_date=date(2026, 4, 9))

    assert html, "build_digest_html returned an empty string"
    assert "Understanding LLM Agents in 2026" in html


# ---------------------------------------------------------------------------
# 2. test_build_html_courses_section
# ---------------------------------------------------------------------------

def test_build_html_courses_section() -> None:
    """When at least one item is a course, the HTML contains the COURSES heading.

    The ``{% if courses %}`` block in the template must render the
    "COURSES & LEARNING RESOURCES" section header.
    """
    course = _make_item(
        title="Deep Learning Specialization",
        is_course=True,
    )
    html = build_digest_html([course], digest_date=date(2026, 4, 9))

    assert html, "build_digest_html returned an empty string"
    assert "COURSES" in html
    assert "Deep Learning Specialization" in html


# ---------------------------------------------------------------------------
# 3. test_build_html_no_courses_section_when_empty
# ---------------------------------------------------------------------------

def test_build_html_no_courses_section_when_empty() -> None:
    """When no items are courses, the COURSES section heading is not rendered.

    Verifies that the ``{% if courses %}`` guard in the template suppresses
    the entire courses block when the list is empty.
    """
    article = _make_item(title="Regular Article", is_course=False)
    html = build_digest_html([article], digest_date=date(2026, 4, 9))

    assert html, "build_digest_html returned an empty string"
    # The heading text should not appear at all.
    assert "COURSES" not in html


# ---------------------------------------------------------------------------
# 4. test_subject_line_format
# ---------------------------------------------------------------------------

def test_subject_line_format(minimal_config: Config) -> None:
    """_build_subject returns the correct format with count and date.

    Expected pattern: ``🍺 NewsBrewer | Wednesday, April 9 · 5 articles``
    """
    agent = DigestAgent(minimal_config)
    items = [_make_item() for _ in range(5)]
    fixed_date = date(2026, 4, 9)  # Thursday

    subject = agent._build_subject(items, fixed_date)

    assert "NewsBrewer" in subject
    assert "April 9" in subject
    assert "5 articles" in subject
    assert "🍺" in subject


def test_subject_line_singular_article(minimal_config: Config) -> None:
    """_build_subject uses singular 'article' when there is exactly one item."""
    agent = DigestAgent(minimal_config)
    items = [_make_item()]
    fixed_date = date(2026, 4, 9)

    subject = agent._build_subject(items, fixed_date)

    assert "1 article" in subject
    assert "articles" not in subject


# ---------------------------------------------------------------------------
# 5. test_send_email_called_with_correct_args
# ---------------------------------------------------------------------------

def test_send_email_called_with_correct_args(minimal_config: Config) -> None:
    """_send_email calls starttls() and sendmail() on the SMTP server object.

    :class:`smtplib.SMTP` is patched so no real network connection is made.
    The test verifies that the mock server's ``starttls`` and ``sendmail``
    methods are each called exactly once with the expected arguments.
    """
    mock_server = MagicMock()
    # Make the context-manager protocol work: __enter__ returns mock_server.
    mock_smtp_cls = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = mock_server
    mock_smtp_cls.return_value.__exit__.return_value = False

    html = "<html><body>Test digest</body></html>"
    subject = "Test Subject"

    with patch("src.agents.digest_agent.smtplib.SMTP", mock_smtp_cls):
        agent = DigestAgent(minimal_config)
        agent._send_email(html, subject)

    # SMTP was instantiated with the configured host and port.
    mock_smtp_cls.assert_called_once_with(
        minimal_config.delivery.smtp_server,
        minimal_config.delivery.smtp_port,
    )

    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with(
        minimal_config.delivery.smtp_email,
        minimal_config.delivery.smtp_password,
    )
    mock_server.sendmail.assert_called_once()

    # Verify sendmail was called with the correct from/to addresses.
    call_args = mock_server.sendmail.call_args
    assert call_args[0][0] == minimal_config.delivery.smtp_email
    assert call_args[0][1] == [minimal_config.delivery.send_to]


# ---------------------------------------------------------------------------
# 6. test_digest_agent_handles_smtp_error
# ---------------------------------------------------------------------------

def test_digest_agent_handles_smtp_error(minimal_config: Config) -> None:
    """When SMTP raises an exception, DigestAgent does not crash.

    :class:`smtplib.SMTP` is patched to raise :class:`smtplib.SMTPException`
    on ``__enter__``.  The agent must catch the error, log it, and return
    without re-raising.
    """
    mock_smtp_cls = MagicMock()
    mock_smtp_cls.return_value.__enter__.side_effect = smtplib.SMTPException(
        "Connection refused"
    )

    html = "<html><body>Test digest</body></html>"
    subject = "Test Subject"

    with patch("src.agents.digest_agent.smtplib.SMTP", mock_smtp_cls):
        agent = DigestAgent(minimal_config)
        # Must not raise — the agent swallows and logs the exception.
        agent._send_email(html, subject)
