"""Data model for an email message fetched from an IMAP account."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class EmailMessage:
    """Represents a single email message retrieved from an IMAP mailbox.

    Attributes:
        uid: Unique identifier string for the message within the mailbox.
        subject: The email subject line.
        sender: The From address of the email.
        date: The parsed date/time the email was sent.
        body_text: Plain-text body of the email (may be empty string).
        body_html: HTML body of the email (may be empty string).
        urls: Deduplicated list of HTTP/HTTPS URLs extracted from the email.
        account_name: Human-readable name of the IMAP account this came from.
        is_manual: True when the email was sent with the BREW: keyword in
            the subject line, triggering a manual one-off digest.
        manual_note: The portion of the subject after the "BREW:" keyword,
            used as a contextual note for the manual digest.
    """

    uid: str
    subject: str
    sender: str
    date: datetime
    body_text: str
    body_html: str
    urls: list[str]
    account_name: str
    is_manual: bool = False  # True if from BREW: email
    manual_note: str = ""    # Subject text after "BREW:" keyword
