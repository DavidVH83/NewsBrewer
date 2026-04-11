"""Agent that detects BREW: self-sent emails for manual link ingestion.

Users can add a link to the digest on demand by sending an email to themselves
with a subject that starts with the configured manual keyword (default
``"BREW"``).  The body should contain the URL to include, and the subject line
after the colon becomes an optional note shown alongside the link in the digest.

Example trigger email::

    To: me@example.com
    From: me@example.com
    Subject: BREW: Interesting article on AI agents

    https://example.com/interesting-article

Typical usage::

    from src.agents.manual_link_agent import ManualLinkAgent
    from src.utils.config_loader import load_config

    config = load_config()
    agent = ManualLinkAgent(config)
    messages = agent.run()
"""

import email
import email.header
import email.utils
from datetime import datetime, timedelta, timezone
from email.message import Message

from src.models.email_message import EmailMessage
from src.utils.config_loader import AccountConfig, Config
from src.utils.imap_helper import connect, disconnect
from src.utils.logger import get_logger
from src.utils.url_extractor import extract_from_text

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _decode_header_value(raw_value: str | None) -> str:
    """Decode an RFC 2047-encoded email header value to a plain Unicode string.

    Args:
        raw_value: Raw header value as returned by the :mod:`email` module.

    Returns:
        Decoded Unicode string, or ``""`` if *raw_value* is ``None`` or
        decoding fails.
    """
    if not raw_value:
        return ""
    try:
        parts = email.header.decode_header(raw_value)
        decoded_parts: list[str] = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded_parts.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                decoded_parts.append(part)
        return "".join(decoded_parts)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not decode header value '%s': %s", raw_value, exc)
        return raw_value or ""


def _parse_date(date_str: str | None) -> datetime:
    """Parse an email Date header string into a timezone-aware datetime.

    Falls back to the current UTC time when parsing fails.

    Args:
        date_str: Raw value of the ``Date:`` header.

    Returns:
        A timezone-aware :class:`datetime` object.
    """
    if not date_str:
        return datetime.now(tz=timezone.utc)
    try:
        parsed = email.utils.parsedate_to_datetime(date_str)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not parse date '%s': %s — using current UTC time.", date_str, exc)
        return datetime.now(tz=timezone.utc)


def _extract_plain_text(msg: Message) -> str:
    """Extract the plain-text body from a MIME message.

    For multipart messages, returns the first ``text/plain`` part found.
    For non-multipart messages, returns the payload directly if it is
    plain text.

    Args:
        msg: Parsed :class:`email.message.Message` object.

    Returns:
        Plain-text body as a Unicode string, or ``""`` if none is found.
    """
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True)
                if payload is not None:
                    try:
                        return payload.decode(charset, errors="replace")
                    except (LookupError, UnicodeDecodeError):
                        return payload.decode("utf-8", errors="replace")
    else:
        if msg.get_content_type() == "text/plain":
            charset = msg.get_content_charset() or "utf-8"
            payload = msg.get_payload(decode=True)
            if payload is not None:
                try:
                    return payload.decode(charset, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    return payload.decode("utf-8", errors="replace")
    return ""


def _extract_manual_note(subject: str, keyword: str) -> str:
    """Extract the note text from a BREW subject line.

    Looks for ``<keyword>:`` at the start of the subject (case-insensitive)
    and returns everything after it, stripped of leading/trailing whitespace.
    If the subject is just the keyword with no colon or note, an empty string
    is returned.

    Args:
        subject: Decoded email subject line.
        keyword: The manual trigger keyword (e.g. ``"BREW"``).

    Returns:
        Note text extracted from the subject, or ``""`` if no note is present.

    Examples::

        _extract_manual_note("BREW: Great AI article", "BREW")
        # -> "Great AI article"

        _extract_manual_note("BREW", "BREW")
        # -> ""
    """
    prefix_with_colon = keyword.lower() + ":"
    subject_stripped = subject.strip()
    if subject_stripped.lower().startswith(prefix_with_colon):
        return subject_stripped[len(prefix_with_colon):].strip()
    return ""


def _build_imap_search_criteria(account_email: str, keyword: str, since_date: str) -> str:
    """Build the IMAP SEARCH command criteria string.

    Constructs a search query that matches emails:
    - With a subject containing the manual keyword.
    - Received on or after *since_date*.
    - From any sender — allows forwarding from Outlook or other addresses.

    Args:
        account_email: The account's email address (unused, kept for API compatibility).
        keyword: Manual trigger keyword to search for in the subject.
        since_date: Date string in ``DD-Mon-YYYY`` format (e.g. ``"08-Apr-2026"``).

    Returns:
        IMAP search criteria string ready to pass to :meth:`imaplib.IMAP4.search`.
    """
    return f'(SUBJECT "{keyword}" SINCE "{since_date}")'


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class ManualLinkAgent:
    """Finds self-sent BREW: emails and wraps them as manual EmailMessage objects.

    Users trigger a manual digest entry by forwarding any email (from any
    address) to the configured Gmail inbox with a subject containing the
    manual keyword (default ``"BREW"``).  Forwarding from Outlook or another
    address works fine.  The agent searches the INBOX for any email with the
    keyword in the subject from the last 24 hours, extracts the first URL
    from the body, and records the note text from the subject line.

    Args:
        config: Fully populated :class:`~src.utils.config_loader.Config` object.

    Example::

        agent = ManualLinkAgent(config)
        manual_messages = agent.run()
    """

    def __init__(self, config: Config) -> None:
        """Initialise the agent with pipeline configuration.

        Args:
            config: Application configuration including IMAP account details
                and the manual trigger keyword.
        """
        self._config = config
        self._logger = get_logger(__name__)

    def run(self) -> list[EmailMessage]:
        """Find BREW: emails sent by the user to themselves.

        For each configured IMAP account the agent:

        1. Connects via IMAP SSL.
        2. Searches for self-sent emails matching the manual keyword in the
           subject, received in the last 24 hours.
        3. For each match, parses the email, extracts the first URL from the
           body, and extracts the note text from the subject.
        4. Creates an :class:`~src.models.email_message.EmailMessage` with
           ``is_manual=True``.
        5. Disconnects cleanly.

        If an account fails to connect the error is logged and the agent
        continues with the next account.  If an individual email fails to
        parse, a warning is logged and that email is skipped.

        Returns:
            List of :class:`~src.models.email_message.EmailMessage` objects
            with ``is_manual=True`` for each qualifying BREW: email.  May be
            empty if no manual emails were found or all accounts failed.
        """
        all_messages: list[EmailMessage] = []
        keyword = self._config.manual_keyword
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)
        since_date = cutoff.strftime("%d-%b-%Y")

        for account in self._config.email_sources:
            self._logger.info(
                "ManualLinkAgent: checking account '%s' for BREW emails.", account.name
            )

            mail = connect(
                server=account.imap_server,
                port=account.imap_port,
                email=account.email,
                password=account.app_password,
            )

            if mail is None:
                self._logger.error(
                    "ManualLinkAgent: could not connect to '%s' — skipping.", account.name
                )
                continue

            try:
                # Select INBOX in read-only mode.
                try:
                    status, _ = mail.select("INBOX", readonly=True)
                    if status != "OK":
                        self._logger.error(
                            "ManualLinkAgent: could not select INBOX on '%s'.", account.name
                        )
                        continue
                except Exception as exc:  # noqa: BLE001
                    self._logger.error(
                        "ManualLinkAgent: error selecting INBOX on '%s': %s", account.name, exc
                    )
                    continue

                # Build and execute the IMAP search.
                criteria = _build_imap_search_criteria(account.email, keyword, since_date)
                self._logger.debug(
                    "ManualLinkAgent: IMAP search criteria for '%s': %s", account.name, criteria
                )

                try:
                    status, data = mail.search(None, criteria)
                except Exception as exc:  # noqa: BLE001
                    self._logger.error(
                        "ManualLinkAgent: IMAP search failed on '%s': %s", account.name, exc
                    )
                    continue

                if status != "OK" or not data or not data[0]:
                    self._logger.info(
                        "ManualLinkAgent: no BREW emails found in '%s'.", account.name
                    )
                    continue

                uid_list: list[bytes] = data[0].split()
                self._logger.info(
                    "ManualLinkAgent: found %d BREW email(s) in '%s'.",
                    len(uid_list),
                    account.name,
                )

                for uid in uid_list:
                    msg_or_none = self._parse_brew_email(uid, mail, account)
                    if msg_or_none is not None:
                        all_messages.append(msg_or_none)

            finally:
                disconnect(mail)

        self._logger.info(
            "ManualLinkAgent.run() complete — %d manual message(s) found.", len(all_messages)
        )
        return all_messages

    def _parse_brew_email(
        self,
        uid: bytes,
        mail,  # imaplib.IMAP4_SSL
        account: AccountConfig,
    ) -> EmailMessage | None:
        """Fetch and parse a single BREW: email by UID.

        Fetches raw RFC 822 bytes, parses MIME structure, extracts the first
        URL from the plain-text body, and builds the note from the subject.

        Args:
            uid: Message UID byte string.
            mail: Active authenticated IMAP connection.
            account: Account configuration this message belongs to.

        Returns:
            An :class:`~src.models.email_message.EmailMessage` with
            ``is_manual=True``, or ``None`` if fetching or parsing fails.
        """
        uid_str = uid.decode("ascii", errors="replace")

        try:
            status, data = mail.fetch(uid, "(RFC822)")
            if status != "OK" or not data:
                self._logger.warning(
                    "ManualLinkAgent: failed to fetch UID %s from '%s'.", uid_str, account.name
                )
                return None

            raw_email: bytes | None = None
            for item in data:
                if isinstance(item, tuple) and len(item) >= 2:
                    raw_email = item[1]
                    break

            if not raw_email:
                self._logger.warning(
                    "ManualLinkAgent: no payload for UID %s in '%s'.", uid_str, account.name
                )
                return None

            msg: Message = email.message_from_bytes(raw_email)

        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "ManualLinkAgent: could not fetch UID %s from '%s': %s — skipping.",
                uid_str,
                account.name,
                exc,
            )
            return None

        try:
            raw_from = msg.get("From", "")
            raw_subject = msg.get("Subject", "")
            raw_date = msg.get("Date", "")

            sender_raw = _decode_header_value(raw_from)
            _, sender_addr = email.utils.parseaddr(sender_raw)
            sender = sender_addr.lower().strip() or sender_raw.lower()

            subject = _decode_header_value(raw_subject)
            date = _parse_date(raw_date)

            body_text = _extract_plain_text(msg)

            # Extract first usable URL from the body.
            all_urls = extract_from_text(body_text)
            first_url = all_urls[0] if all_urls else ""

            if not first_url:
                self._logger.warning(
                    "ManualLinkAgent: UID %s in '%s' has no extractable URL — skipping.",
                    uid_str,
                    account.name,
                )
                return None

            note = _extract_manual_note(subject, self._config.manual_keyword)

            email_message = EmailMessage(
                uid=uid_str,
                subject=subject,
                sender=sender,
                date=date,
                body_text=body_text,
                body_html="",
                urls=[first_url],
                account_name=account.name,
                is_manual=True,
                manual_note=note,
            )

            self._logger.info(
                "ManualLinkAgent: parsed BREW email UID %s from '%s' — note='%s', url='%s'.",
                uid_str,
                account.name,
                note,
                first_url,
            )
            return email_message

        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "ManualLinkAgent: error processing UID %s from '%s': %s — skipping.",
                uid_str,
                account.name,
                exc,
            )
            return None
