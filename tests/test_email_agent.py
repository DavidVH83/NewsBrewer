"""Tests for EmailAgent and ManualLinkAgent.

All tests use mock IMAP connections — no real network calls are made.
The sample_email.txt fixture provides a realistic multipart newsletter email
in RFC 2822 format.

Run with::

    pytest tests/test_email_agent.py -v
"""

import os
import imaplib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agents.email_agent import EmailAgent
from src.agents.manual_link_agent import ManualLinkAgent
from src.models.email_message import EmailMessage
from src.utils.config_loader import (
    AccountConfig,
    AIFocusConfig,
    Config,
    DeliveryConfig,
    ModelConfig,
)


# ---------------------------------------------------------------------------
# Fixtures — paths and raw bytes
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_EMAIL_PATH = FIXTURES_DIR / "sample_email.txt"


@pytest.fixture(scope="session")
def sample_email_bytes() -> bytes:
    """Load the sample newsletter email from the fixture file."""
    return SAMPLE_EMAIL_PATH.read_bytes()


@pytest.fixture()
def minimal_config() -> Config:
    """Return a minimal Config with one account and a short newsletter_senders list.

    Credentials are dummy values — no real IMAP connections are made.
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
        newsletter_senders=["noreply@medium.com", "hello@agenticengineering.com"],
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


# ---------------------------------------------------------------------------
# IMAP mock factory
# ---------------------------------------------------------------------------

def make_mock_imap(email_bytes: bytes) -> MagicMock:
    """Return an IMAP4_SSL mock that simulates a single-message INBOX.

    The mock is spec'd against :class:`imaplib.IMAP4_SSL` so that only real
    IMAP methods are accessible.

    Args:
        email_bytes: Raw RFC 2822 email bytes to return from ``fetch()``.

    Returns:
        Configured :class:`~unittest.mock.MagicMock` instance.
    """
    mock_mail = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_mail.select.return_value = ("OK", [b"1"])
    mock_mail.search.return_value = ("OK", [b"1"])
    mock_mail.fetch.return_value = ("OK", [(b"1 (RFC822 {size})", email_bytes)])
    mock_mail.logout.return_value = ("BYE", [])
    return mock_mail


def make_brew_email_bytes(
    account_email: str = "test@gmail.com",
    subject: str = "BREW: Interesting AI article",
    url: str = "https://example.com/ai-article",
) -> bytes:
    """Build a minimal raw BREW: email in RFC 2822 format.

    Args:
        account_email: The From/To address (self-sent).
        subject: Subject line including the BREW keyword.
        url: URL to place in the plain-text body.

    Returns:
        Raw email bytes suitable for passing to ``make_mock_imap()``.
    """
    raw = (
        f"MIME-Version: 1.0\r\n"
        f"Date: Wed, 09 Apr 2026 09:00:00 +0000\r\n"
        f"Message-ID: <brew123@test.com>\r\n"
        f"From: {account_email}\r\n"
        f"To: {account_email}\r\n"
        f"Subject: {subject}\r\n"
        f"Content-Type: text/plain; charset=\"utf-8\"\r\n"
        f"\r\n"
        f"Check this out:\r\n"
        f"{url}\r\n"
    )
    return raw.encode("utf-8")


# ---------------------------------------------------------------------------
# 1. test_url_extraction_from_plain_text
# ---------------------------------------------------------------------------

def test_url_extraction_from_plain_text() -> None:
    """URLs in the text/plain part of an email are extracted correctly.

    Verifies that :func:`~src.utils.url_extractor.extract_from_text` finds
    the known article URLs present in the sample email plain-text body.
    """
    from src.utils.url_extractor import extract_from_text

    plain_text = (
        "Read the full breakdown:\n"
        "https://anthropic.com/news/claude-3-5-sonnet\n"
        "\n"
        "A step-by-step guide:\n"
        "https://medium.com/towards-data-science/langgraph-agentic-pipelines-abc123\n"
    )

    urls = extract_from_text(plain_text)

    assert "https://anthropic.com/news/claude-3-5-sonnet" in urls
    assert "https://medium.com/towards-data-science/langgraph-agentic-pipelines-abc123" in urls
    # No tracking / unsubscribe links should appear.
    assert not any("/unsubscribe" in u for u in urls)


# ---------------------------------------------------------------------------
# 2. test_url_extraction_from_html
# ---------------------------------------------------------------------------

def test_url_extraction_from_html() -> None:
    """URLs in <a href> tags within the HTML part are extracted correctly.

    Verifies that :func:`~src.utils.url_extractor.extract_from_html` returns
    article links and that unsubscribe/preference links are filtered out.
    """
    from src.utils.url_extractor import extract_from_html

    html = """
    <html><body>
      <a href="https://anthropic.com/news/claude-3-5-sonnet">Read</a>
      <a href="https://medium.com/ml-practitioner/rag-vs-finetuning-def456">RAG article</a>
      <a href="https://medium.com/unsubscribe?token=abc">Unsubscribe</a>
      <a href="https://medium.com/email-preferences">Preferences</a>
    </body></html>
    """

    urls = extract_from_html(html)

    assert "https://anthropic.com/news/claude-3-5-sonnet" in urls
    assert "https://medium.com/ml-practitioner/rag-vs-finetuning-def456" in urls
    # Unsubscribe and preferences links must be filtered.
    assert not any("/unsubscribe" in u for u in urls)
    assert not any("/email-preferences" in u for u in urls)


# ---------------------------------------------------------------------------
# 3. test_sender_filtering_known_sender
# ---------------------------------------------------------------------------

def test_sender_filtering_known_sender(
    sample_email_bytes: bytes,
    minimal_config: Config,
) -> None:
    """Emails from a known newsletter sender are included in the results.

    The sample email is from ``noreply@medium.com`` which is in
    ``minimal_config.newsletter_senders``.  The agent must return exactly
    one :class:`~src.models.email_message.EmailMessage`.
    """
    mock_mail = make_mock_imap(sample_email_bytes)

    with patch("src.agents.email_agent.connect", return_value=mock_mail), \
         patch("src.agents.email_agent.disconnect"):

        agent = EmailAgent(minimal_config)
        messages = agent.run()

    assert len(messages) == 1
    msg = messages[0]
    assert isinstance(msg, EmailMessage)
    assert "medium.com" in msg.sender
    assert msg.is_manual is False
    assert len(msg.urls) > 0


# ---------------------------------------------------------------------------
# 4. test_sender_filtering_unknown_sender
# ---------------------------------------------------------------------------

def test_sender_filtering_unknown_sender(
    minimal_config: Config,
) -> None:
    """All senders are now accepted — the agent scans every email.

    Sender whitelisting was removed so that AI decides relevance, not the
    sender address.  An email from an unknown sender must still be collected.
    """
    unknown_sender_email = (
        "MIME-Version: 1.0\r\n"
        "Date: Wed, 09 Apr 2026 08:00:00 +0000\r\n"
        "From: Spam Corp <unknown@spammer.example>\r\n"
        "To: test@gmail.com\r\n"
        "Subject: Buy our stuff!\r\n"
        "Content-Type: text/plain; charset=\"utf-8\"\r\n"
        "\r\n"
        "Click here: https://spammer.example/buy-now\r\n"
    ).encode("utf-8")

    mock_mail = make_mock_imap(unknown_sender_email)

    with patch("src.agents.email_agent.connect", return_value=mock_mail), \
         patch("src.agents.email_agent.disconnect"):

        agent = EmailAgent(minimal_config)
        messages = agent.run()

    # All senders pass through; AI analyst filters by relevance later.
    assert len(messages) == 1
    assert messages[0].sender == "unknown@spammer.example"


# ---------------------------------------------------------------------------
# 5. test_email_agent_handles_connection_error
# ---------------------------------------------------------------------------

def test_email_agent_handles_connection_error(
    minimal_config: Config,
) -> None:
    """When an account fails to connect, the agent logs an error and returns [].

    :func:`~src.utils.imap_helper.connect` is patched to return ``None``
    (its documented failure return value).  The agent must not raise and must
    return an empty list.
    """
    with patch("src.agents.email_agent.connect", return_value=None):
        agent = EmailAgent(minimal_config)
        messages = agent.run()

    assert messages == []


# ---------------------------------------------------------------------------
# 6. test_manual_link_agent_extracts_brew_note
# ---------------------------------------------------------------------------

def test_manual_link_agent_extracts_brew_note(
    minimal_config: Config,
) -> None:
    """A BREW: email yields a manual EmailMessage with the correct note and URL.

    Sends a self-addressed email with subject ``"BREW: Interesting AI article"``
    containing one article URL.  The agent must return one
    :class:`~src.models.email_message.EmailMessage` with ``is_manual=True``
    and ``manual_note="Interesting AI article"``.
    """
    article_url = "https://example.com/ai-article-unique"
    brew_bytes = make_brew_email_bytes(
        account_email="test@gmail.com",
        subject="BREW: Interesting AI article",
        url=article_url,
    )
    mock_mail = make_mock_imap(brew_bytes)

    with patch("src.agents.manual_link_agent.connect", return_value=mock_mail), \
         patch("src.agents.manual_link_agent.disconnect"):

        agent = ManualLinkAgent(minimal_config)
        messages = agent.run()

    assert len(messages) == 1
    msg = messages[0]
    assert isinstance(msg, EmailMessage)
    assert msg.is_manual is True
    assert msg.manual_note == "Interesting AI article"
    assert article_url in msg.urls


# ---------------------------------------------------------------------------
# Bonus: test that the sample fixture file loads and is valid RFC 2822
# ---------------------------------------------------------------------------

def test_sample_fixture_is_valid_email(sample_email_bytes: bytes) -> None:
    """The sample_email.txt fixture parses as a valid RFC 2822 email.

    Ensures the fixture file exists, is non-empty, and has the expected
    From/Subject headers.
    """
    import email as email_lib

    assert len(sample_email_bytes) > 0, "Fixture file is empty"

    msg = email_lib.message_from_bytes(sample_email_bytes)
    assert msg["From"] is not None
    assert "medium.com" in msg["From"].lower()
    assert "AI" in msg["Subject"]
    assert msg.is_multipart()
