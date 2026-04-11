"""Email agent that fetches emails from configured IMAP accounts.

Iterates over every IMAP account in :class:`~src.utils.config_loader.Config`,
connects via SSL, retrieves ALL emails from the last 24 hours, extracts URLs,
and returns a flat list of :class:`~src.models.email_message.EmailMessage`
objects ready for the AI analyst stage, which determines relevance.

Typical usage::

    from src.agents.email_agent import EmailAgent
    from src.utils.config_loader import load_config

    config = load_config()
    agent = EmailAgent(config)
    messages = agent.run()
"""

import email
import email.header
import email.utils
from datetime import datetime, timezone
from email.message import Message

from src.models.email_message import EmailMessage
from src.utils.config_loader import AccountConfig, Config
from src.utils.imap_helper import connect, disconnect, search_since
from src.utils.logger import get_logger
from src.utils.url_extractor import extract_urls

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _decode_header_value(raw_value: str | None) -> str:
    """Decode an RFC 2047-encoded email header value to a plain string.

    Handles multi-part encoded-word sequences (e.g. ``=?utf-8?b?...?=``) and
    returns a clean Unicode string.  Falls back to an empty string when the
    value is ``None`` or cannot be decoded.

    Args:
        raw_value: Raw header value as returned by :mod:`email.header`.

    Returns:
        Decoded Unicode string, or ``""`` on failure.
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
    """Parse an email Date header string into a timezone-aware :class:`datetime`.

    Uses :func:`email.utils.parsedate_to_datetime` for RFC 2822 parsing.
    Falls back to the current UTC time if parsing fails so that the
    resulting :class:`~src.models.email_message.EmailMessage` always has a
    valid ``date`` field.

    Args:
        date_str: Raw value of the ``Date:`` header.

    Returns:
        A timezone-aware :class:`datetime` object.
    """
    if not date_str:
        return datetime.now(tz=timezone.utc)
    try:
        parsed = email.utils.parsedate_to_datetime(date_str)
        # Ensure the datetime is timezone-aware; treat naive as UTC.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not parse date '%s': %s — using current UTC time.", date_str, exc)
        return datetime.now(tz=timezone.utc)


def _extract_sender_address(from_header: str) -> str:
    """Extract the bare email address from a From header.

    Handles display-name formats such as ``"Newsletter Bot" <news@example.com>``
    and returns just the lowercased address portion.

    Args:
        from_header: Raw value of the ``From:`` header.

    Returns:
        Lowercased email address string, or the original header value if
        parsing fails.
    """
    try:
        _name, address = email.utils.parseaddr(from_header)
        return address.lower().strip() if address else from_header.lower().strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not parse From header '%s': %s", from_header, exc)
        return from_header.lower().strip()


def _extract_body(msg: Message) -> tuple[str, str]:
    """Walk a MIME message tree and extract plain-text and HTML body parts.

    For multipart messages, iterates over every part and collects the first
    ``text/plain`` and ``text/html`` payloads.  For non-multipart messages,
    reads the payload directly according to the content type.

    Args:
        msg: Parsed :class:`email.message.Message` object.

    Returns:
        Tuple of ``(body_text, body_html)`` where either element may be an
        empty string if that MIME part is absent.
    """
    body_text: str = ""
    body_html: str = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            # Skip attachments.
            if "attachment" in content_disposition:
                continue

            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True)
            if payload is None:
                continue

            try:
                decoded = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                decoded = payload.decode("utf-8", errors="replace")

            if content_type == "text/plain" and not body_text:
                body_text = decoded
            elif content_type == "text/html" and not body_html:
                body_html = decoded
    else:
        content_type = msg.get_content_type()
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        if payload is not None:
            try:
                decoded = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                decoded = payload.decode("utf-8", errors="replace")
            if content_type == "text/plain":
                body_text = decoded
            elif content_type == "text/html":
                body_html = decoded

    return body_text, body_html


def _is_known_sender(sender_address: str, newsletter_senders: list[str]) -> bool:
    """Check whether a sender address appears in the allowed newsletter senders list.

    Comparison is case-insensitive.

    Args:
        sender_address: Lowercased sender email address extracted from the
            ``From:`` header.
        newsletter_senders: List of allowed sender addresses from
            :attr:`~src.utils.config_loader.Config.newsletter_senders`.

    Returns:
        ``True`` when *sender_address* matches any entry in *newsletter_senders*.
    """
    sender_lower = sender_address.lower()
    return any(sender_lower == s.lower() for s in newsletter_senders)


def _fetch_and_parse_email(
    mail,  # imaplib.IMAP4_SSL — avoid importing just for type annotation here
    uid: bytes,
    account: AccountConfig,
    newsletter_senders: list[str],  # kept for signature compatibility, not used for filtering
) -> EmailMessage | None:
    """Fetch a single email by UID, parse it, and return an EmailMessage.

    Fetches the raw RFC 822 bytes using IMAP FETCH, parses the MIME structure,
    decodes headers and body parts, and extracts URLs. All emails are returned —
    relevance filtering is done by the AI analyst stage.

    Args:
        mail: Authenticated IMAP connection.
        uid: Message UID as returned by :func:`~src.utils.imap_helper.search_since`.
        account: The :class:`~src.utils.config_loader.AccountConfig` this
            message came from (used for ``account_name``).
        newsletter_senders: Unused — kept for API compatibility.

    Returns:
        Populated :class:`~src.models.email_message.EmailMessage` on success,
        or ``None`` if fetching or parsing fails.
    """
    uid_str = uid.decode("ascii", errors="replace")

    try:
        logger.debug("Fetching UID %s from %s", uid_str, account.name)
        status, data = mail.fetch(uid, "(RFC822)")
        if status != "OK" or not data:
            logger.warning("Failed to fetch UID %s from %s: status=%s", uid_str, account.name, status)
            return None

        # data may be [(b'N (RFC822 {size})', raw_bytes), b')'] — find the bytes.
        raw_email: bytes | None = None
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2:
                raw_email = item[1]
                break

        if not raw_email:
            logger.warning("No RFC822 payload found for UID %s in %s", uid_str, account.name)
            return None

        msg: Message = email.message_from_bytes(raw_email)

    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch/parse UID %s from %s: %s", uid_str, account.name, exc)
        return None

    try:
        # Decode headers.
        raw_from = msg.get("From", "")
        raw_subject = msg.get("Subject", "")
        raw_date = msg.get("Date", "")

        sender = _extract_sender_address(_decode_header_value(raw_from))
        subject = _decode_header_value(raw_subject)
        date = _parse_date(raw_date)

        # Extract body parts.
        body_text, body_html = _extract_body(msg)

        # Extract URLs.
        urls = extract_urls(text=body_text, html=body_html)

        email_message = EmailMessage(
            uid=uid_str,
            subject=subject,
            sender=sender,
            date=date,
            body_text=body_text,
            body_html=body_html,
            urls=urls,
            account_name=account.name,
            is_manual=False,
            manual_note="",
        )

        logger.info(
            "Parsed email UID %s from '%s' (%s) — %d URL(s) extracted.",
            uid_str,
            sender,
            account.name,
            len(urls),
        )
        return email_message

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Error processing UID %s from %s: %s — skipping this email.",
            uid_str,
            account.name,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class EmailAgent:
    """Fetches newsletter emails from all configured IMAP accounts.

    On each call to :meth:`run`, the agent connects to every account listed
    in :attr:`~src.utils.config_loader.Config.email_sources`, searches for
    emails received in the last 24 hours, parses MIME messages, and filters
    by the :attr:`~src.utils.config_loader.Config.newsletter_senders` allow-list.
    Accounts that fail to connect are skipped without raising an exception.

    Args:
        config: Fully populated :class:`~src.utils.config_loader.Config` object.

    Example::

        agent = EmailAgent(config)
        messages = agent.run()
        print(f"Collected {len(messages)} newsletter emails")
    """

    def __init__(self, config: Config) -> None:
        """Initialise the agent with pipeline configuration.

        Args:
            config: Application configuration including IMAP account details,
                newsletter sender allow-list, and the manual trigger keyword.
        """
        self._config = config
        self._logger = get_logger(__name__)

    def run(self) -> list[EmailMessage]:
        """Run email agent: connect to all accounts, find newsletter emails from last 24h.

        For each configured account the agent:

        1. Connects via IMAP SSL using :func:`~src.utils.imap_helper.connect`.
        2. Searches for emails from the last 24 hours.
        3. Fetches and parses each message.
        4. Skips messages whose sender is not in ``newsletter_senders``.
        5. Extracts URLs from qualifying messages.
        6. Disconnects cleanly.

        If an account fails to connect the error is logged and processing
        continues with the next account.  If an individual email fails to parse,
        a warning is logged and that email is skipped.

        Returns:
            Flat list of :class:`~src.models.email_message.EmailMessage` objects
            collected across all accounts.  May be empty if no qualifying emails
            were found or all accounts failed.
        """
        all_messages: list[EmailMessage] = []

        for account in self._config.email_sources:
            self._logger.info(
                "Processing account: %s (%s)", account.name, account.imap_server
            )

            mail = connect(
                server=account.imap_server,
                port=account.imap_port,
                email=account.email,
                password=account.app_password,
            )

            if mail is None:
                self._logger.error(
                    "Could not connect to account '%s' — skipping.", account.name
                )
                continue

            try:
                uids = search_since(mail, hours=24)
                self._logger.info(
                    "Account '%s': %d email(s) to examine.", account.name, len(uids)
                )

                for uid in uids:
                    msg = _fetch_and_parse_email(
                        mail=mail,
                        uid=uid,
                        account=account,
                        newsletter_senders=self._config.newsletter_senders,
                    )
                    if msg is not None:
                        all_messages.append(msg)

            finally:
                disconnect(mail)

        self._logger.info(
            "EmailAgent.run() complete — %d newsletter email(s) collected across %d account(s).",
            len(all_messages),
            len(self._config.email_sources),
        )
        return all_messages
