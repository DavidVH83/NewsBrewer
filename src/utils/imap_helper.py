"""Low-level IMAP helper functions for the NewsBrewer pipeline.

Provides thin wrappers around Python's built-in :mod:`imaplib` to handle
SSL connection, safe disconnection, and date-bounded email search.  All
network errors are caught internally; callers receive ``None`` or an empty
list rather than raw exceptions so that the pipeline can keep running even
when one mail server is unreachable.

Typical usage::

    from src.utils.imap_helper import connect, disconnect, search_since

    mail = connect(server, port, email, password)
    if mail is not None:
        uids = search_since(mail, hours=24)
        # … fetch and parse emails …
        disconnect(mail)
"""

import imaplib
import ssl
import time
from datetime import datetime, timedelta, timezone

from src.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_RETRIES: int = 3
_RETRY_DELAY_SECONDS: float = 2.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def connect(
    server: str,
    port: int,
    email: str,
    password: str,
) -> imaplib.IMAP4_SSL | None:
    """Connect to an IMAP server with SSL and authenticate.

    Attempts the connection up to three times with a two-second delay between
    retries.  Uses the default SSL context (certificate verification enabled)
    which requires a valid server certificate.

    All credentials must be supplied by the caller; this function never reads
    environment variables directly.

    Args:
        server: Hostname of the IMAP server (e.g. ``"imap.gmail.com"``).
        port: TCP port to connect on — typically ``993`` for IMAPS.
        email: Full email address used for authentication.
        password: App-specific or account password for authentication.

    Returns:
        An authenticated :class:`imaplib.IMAP4_SSL` instance on success,
        or ``None`` if all retry attempts fail.

    Example::

        mail = connect("imap.gmail.com", 993, "user@gmail.com", "app-password")
        if mail is None:
            print("Could not connect")
    """
    ssl_context = ssl.create_default_context()

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            logger.info(
                "Connecting to IMAP server %s:%d (attempt %d/%d)",
                server,
                port,
                attempt,
                _MAX_RETRIES,
            )
            mail = imaplib.IMAP4_SSL(host=server, port=port, ssl_context=ssl_context)
            mail.login(email, password)
            logger.info("Successfully authenticated as %s on %s", email, server)
            return mail
        except imaplib.IMAP4.error as exc:
            logger.error(
                "IMAP authentication error for %s on %s (attempt %d/%d): %s",
                email,
                server,
                attempt,
                _MAX_RETRIES,
                exc,
            )
            # Authentication errors are unlikely to succeed on retry — stop early.
            return None
        except OSError as exc:
            logger.warning(
                "Network error connecting to %s:%d (attempt %d/%d): %s",
                server,
                port,
                attempt,
                _MAX_RETRIES,
                exc,
            )
            if attempt < _MAX_RETRIES:
                logger.debug("Waiting %.1f seconds before next retry…", _RETRY_DELAY_SECONDS)
                time.sleep(_RETRY_DELAY_SECONDS)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Unexpected error connecting to %s:%d (attempt %d/%d): %s",
                server,
                port,
                attempt,
                _MAX_RETRIES,
                exc,
            )
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY_SECONDS)

    logger.error(
        "All %d connection attempts to %s:%d failed. Giving up.",
        _MAX_RETRIES,
        server,
        port,
    )
    return None


def disconnect(mail: imaplib.IMAP4_SSL) -> None:
    """Safely log out and close an IMAP connection.

    Attempts a graceful ``LOGOUT`` command.  If the connection is already
    closed or the server returns an error, the exception is swallowed so that
    callers do not need to handle teardown failures.

    Args:
        mail: An :class:`imaplib.IMAP4_SSL` instance to close.  May already
            be in a disconnected state — this function handles that safely.

    Example::

        disconnect(mail)  # Always safe to call, even if mail is broken
    """
    try:
        mail.logout()
        logger.info("Disconnected from IMAP server.")
    except Exception as exc:  # noqa: BLE001
        # The connection may already be dead; swallow the error.
        logger.debug("Error during IMAP logout (ignored): %s", exc)


def search_since(mail: imaplib.IMAP4_SSL, hours: int = 24) -> list[bytes]:
    """Search the INBOX for emails received within the last *hours* hours.

    Selects the ``INBOX`` mailbox in read-only mode and issues an IMAP
    ``SEARCH SINCE <date>`` command.  The IMAP SINCE criterion matches
    messages whose internal date is on or after midnight of the given date
    (UTC), so results may include messages from up to ~24 hours before the
    precise cutoff.

    Args:
        mail: An authenticated :class:`imaplib.IMAP4_SSL` instance.
        hours: Look-back window in hours.  Defaults to ``24``.

    Returns:
        List of message UID byte strings (e.g. ``[b'1', b'2', b'5']``).
        Returns an empty list if the search fails or no messages match.

    Example::

        uids = search_since(mail, hours=24)
        print(f"Found {len(uids)} recent messages")
    """
    # Compute the cutoff date in UTC and format it as DD-Mon-YYYY (IMAP standard).
    cutoff: datetime = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    since_date: str = cutoff.strftime("%d-%b-%Y")

    try:
        logger.debug("Selecting INBOX (read-only)…")
        status, _ = mail.select("INBOX", readonly=True)
        if status != "OK":
            logger.error("Failed to select INBOX — server responded: %s", status)
            return []

        logger.info("Searching for emails since %s (last %d hours)", since_date, hours)
        status, data = mail.search(None, f'(SINCE "{since_date}")')

        if status != "OK":
            logger.error("IMAP SEARCH command failed with status: %s", status)
            return []

        # data is a list containing a single space-separated byte string of UIDs.
        if not data or not data[0]:
            logger.info("No emails found since %s.", since_date)
            return []

        uid_list: list[bytes] = data[0].split()
        logger.info(
            "Found %d email(s) since %s in INBOX.", len(uid_list), since_date
        )
        return uid_list

    except imaplib.IMAP4.error as exc:
        logger.error("IMAP error during search_since: %s", exc)
        return []
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error during search_since: %s", exc)
        return []
