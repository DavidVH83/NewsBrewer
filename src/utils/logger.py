"""Structured logging utility for the NewsBrewer pipeline.

Creates a logger that writes to both stdout and a daily rotating log file
under the ``logs/`` directory at the project root.  The log file is named
``newsbrewer_YYYY-MM-DD.log``.  Log records include a UTC timestamp, the
log level, and the module name so that pipeline stages are easy to trace.

Usage::

    from src.utils.logger import get_logger

    logger = get_logger(__name__)
    logger.info("Starting digest run")
    logger.warning("Article fetch was partial: %s", url)
    logger.error("Failed to send digest email: %s", exc)
"""

import logging
import os
import sys
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_logs_dir() -> str:
    """Return the absolute path to the ``logs/`` directory.

    The function walks up from this file's location until it finds the
    project root (identified by the presence of ``requirements.txt``), then
    returns ``<root>/logs``.  If the project root cannot be determined, it
    falls back to a ``logs/`` directory relative to the current working
    directory.

    Returns:
        Absolute path string for the logs directory.
    """
    # Start from this file and walk upward looking for the project root marker.
    current = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):  # Limit search depth to avoid infinite loops.
        if os.path.isfile(os.path.join(current, "requirements.txt")):
            return os.path.join(current, "logs")
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    # Fallback: logs/ next to cwd.
    return os.path.join(os.getcwd(), "logs")


def _build_log_filepath(logs_dir: str) -> str:
    """Return the path for today's log file.

    Args:
        logs_dir: Absolute path to the logs directory.

    Returns:
        Full path string, e.g. ``/path/to/logs/newsbrewer_2026-04-09.log``.
    """
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    filename = f"newsbrewer_{today}.log"
    return os.path.join(logs_dir, filename)


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

_FORMATTER = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
# Force UTC timestamps in the formatter.
_FORMATTER.converter = lambda *args: datetime.now(tz=timezone.utc).timetuple()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for the given module name.

    The logger writes at INFO level to stdout and to a date-stamped log file
    in the ``logs/`` directory.  Calling this function multiple times with
    the same *name* returns the same logger instance (standard Python
    behaviour); handlers are only added once.

    Args:
        name: Logger name — pass ``__name__`` from the calling module so
            that log records identify their source correctly.

    Returns:
        A :class:`logging.Logger` instance ready for use.

    Example::

        logger = get_logger(__name__)
        logger.info("Fetching %d articles", len(urls))
    """
    logger = logging.getLogger(name)

    # Only configure handlers the first time this logger is requested.
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # --- stdout handler ---
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.setFormatter(_FORMATTER)
    logger.addHandler(stdout_handler)

    # --- file handler ---
    logs_dir = _resolve_logs_dir()
    try:
        os.makedirs(logs_dir, exist_ok=True)
        log_filepath = _build_log_filepath(logs_dir)
        file_handler = logging.FileHandler(log_filepath, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(_FORMATTER)
        logger.addHandler(file_handler)
    except OSError as exc:
        # If we cannot write logs to disk, warn on stdout but don't crash.
        logger.warning(
            "Could not create log file in '%s': %s. Logging to stdout only.",
            logs_dir,
            exc,
        )

    return logger
