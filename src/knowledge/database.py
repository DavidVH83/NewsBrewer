"""SQLite knowledge base for persisting and querying NewsBrewer digest items.

This module provides the :class:`KnowledgeDatabase` class, which manages a
local SQLite database that stores summarised articles and courses produced by
the NewsBrewer pipeline.  Full-text search is powered by SQLite's built-in
FTS5 extension, allowing fast keyword search across titles, summaries, and
tags without any external dependencies.

The database file is created automatically on first use.  The FTS5 virtual
table is kept in sync with the main ``summaries`` table via an ``AFTER INSERT``
trigger, so callers never need to interact with FTS5 directly.

Usage::

    from src.knowledge.database import KnowledgeDatabase

    db = KnowledgeDatabase()
    db.insert(item)
    results = db.search("retrieval augmented generation")
"""

import json
import sqlite3
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from src.models.digest_item import DigestItem
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------

_DDL_SUMMARIES = """
CREATE TABLE IF NOT EXISTS summaries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            DATE NOT NULL,
    url             TEXT NOT NULL UNIQUE,
    source          TEXT,
    title           TEXT,
    summary         TEXT,
    tags            TEXT,
    is_course       BOOLEAN DEFAULT 0,
    relevance_score REAL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_DDL_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS summaries_fts
USING fts5(title, summary, tags, content=summaries, content_rowid=id);
"""

_DDL_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS summaries_ai AFTER INSERT ON summaries BEGIN
    INSERT INTO summaries_fts(rowid, title, summary, tags)
    VALUES (new.id, new.title, new.summary, new.tags);
END;
"""

_DDL_FEEDBACK = """
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT NOT NULL,
    rating      INTEGER NOT NULL,  -- +1 or -1
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class KnowledgeDatabase:
    """Manages a SQLite database of NewsBrewer digest items.

    The database stores one row per unique article URL.  FTS5 enables
    full-text search across titles, summaries, and tags.  All public methods
    catch their own exceptions and log errors rather than propagating them to
    the caller.

    Args:
        db_path: Path to the SQLite database file.  Intermediate directories
            are created automatically if they do not exist.  Defaults to
            ``data/knowledge_base.db`` relative to the current working
            directory.
    """

    def __init__(self, db_path: str = "data/knowledge_base.db") -> None:
        """Initialise the database connection and create tables if needed.

        Args:
            db_path: Filesystem path for the SQLite database file.
        """
        self._db_path = Path(db_path)
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            logger.info("Opening knowledge database at '%s'", self._db_path)
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._create_schema()
            logger.info("Knowledge database ready")
        except Exception as exc:
            logger.error("Failed to initialise knowledge database: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _create_schema(self) -> None:
        """Create the summaries table, FTS5 virtual table, and insert trigger.

        This is idempotent — all statements use ``CREATE … IF NOT EXISTS``.
        """
        try:
            with self._conn:
                self._conn.execute(_DDL_SUMMARIES)
                self._conn.execute(_DDL_FTS)
                self._conn.execute(_DDL_TRIGGER)
                self._conn.execute(_DDL_FEEDBACK)
            logger.debug("Database schema verified / created")
        except Exception as exc:
            logger.error("Failed to create database schema: %s", exc)
            raise

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        """Convert a sqlite3.Row to a plain dict, deserialising the tags field.

        Args:
            row: A row returned by a ``sqlite3.Cursor``.

        Returns:
            Dictionary with all column values.  The ``tags`` field is decoded
            from its stored JSON string into a Python list.
        """
        d = dict(row)
        try:
            d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
        return d

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def insert(self, item: DigestItem) -> None:
        """Insert a :class:`DigestItem` into the database.

        If a row with the same URL already exists the insert is silently
        skipped (no exception is raised and no existing data is modified).

        Args:
            item: The digest item to persist.
        """
        if self.already_seen(item.url):
            logger.debug("Skipping duplicate URL: %s", item.url)
            return

        tags_json = json.dumps(item.tags) if item.tags else "[]"
        item_date = (
            item.date.date() if isinstance(item.date, datetime) else item.date
        )

        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO summaries
                        (date, url, source, title, summary, tags, is_course, relevance_score)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(item_date),
                        item.url,
                        item.source,
                        item.title,
                        item.summary,
                        tags_json,
                        int(item.is_course),
                        item.relevance_score,
                    ),
                )
            logger.info("Inserted article: %s", item.title)
        except sqlite3.IntegrityError:
            # Race condition: URL inserted between already_seen check and insert.
            logger.debug("Duplicate URL on insert (race condition): %s", item.url)
        except Exception as exc:
            logger.error("Failed to insert item '%s': %s", item.url, exc)

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search articles using SQLite FTS5 full-text search.

        The query is passed directly to FTS5, which supports phrase queries,
        prefix queries, and boolean operators (see SQLite FTS5 documentation).
        Results are ordered by FTS5 relevance rank.

        Args:
            query: Search terms or an FTS5 query expression.
            limit: Maximum number of results to return.  Defaults to 10.

        Returns:
            List of result dicts, each representing one matching article.
            Returns an empty list on error or when no results are found.
        """
        logger.info("Searching for '%s' (limit=%d)", query, limit)
        try:
            cursor = self._conn.execute(
                """
                SELECT s.*
                FROM summaries s
                JOIN summaries_fts f ON s.id = f.rowid
                WHERE summaries_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            )
            rows = [self._row_to_dict(row) for row in cursor.fetchall()]
            logger.info("Search returned %d result(s)", len(rows))
            return rows
        except Exception as exc:
            logger.error("Search failed for query '%s': %s", query, exc)
            return []

    def get_by_date(self, target_date: date) -> list[dict]:
        """Retrieve all articles stored for a specific date.

        Args:
            target_date: The publication / fetch date to filter by.

        Returns:
            List of article dicts for *target_date*, ordered by
            ``relevance_score`` descending.  Returns an empty list on error.
        """
        date_str = str(target_date)
        logger.info("Fetching articles for date %s", date_str)
        try:
            cursor = self._conn.execute(
                "SELECT * FROM summaries WHERE date = ? ORDER BY relevance_score DESC",
                (date_str,),
            )
            rows = [self._row_to_dict(row) for row in cursor.fetchall()]
            logger.info("Found %d article(s) for %s", len(rows), date_str)
            return rows
        except Exception as exc:
            logger.error("get_by_date failed for '%s': %s", date_str, exc)
            return []

    def get_courses(self, limit: int = 20) -> list[dict]:
        """Retrieve course and learning-resource entries.

        Args:
            limit: Maximum number of courses to return.  Defaults to 20.

        Returns:
            List of course dicts sorted by ``relevance_score`` descending.
            Returns an empty list on error.
        """
        logger.info("Fetching top %d courses", limit)
        try:
            cursor = self._conn.execute(
                """
                SELECT * FROM summaries
                WHERE is_course = 1
                ORDER BY relevance_score DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = [self._row_to_dict(row) for row in cursor.fetchall()]
            logger.info("Found %d course(s)", len(rows))
            return rows
        except Exception as exc:
            logger.error("get_courses failed: %s", exc)
            return []

    def already_seen(self, url: str) -> bool:
        """Check whether a URL is already stored in the database.

        Args:
            url: The article URL to look up.

        Returns:
            ``True`` if the URL exists, ``False`` otherwise or on error.
        """
        try:
            cursor = self._conn.execute(
                "SELECT 1 FROM summaries WHERE url = ? LIMIT 1", (url,)
            )
            return cursor.fetchone() is not None
        except Exception as exc:
            logger.error("already_seen check failed for '%s': %s", url, exc)
            return False

    def get_stats(self) -> dict:
        """Return summary statistics about the knowledge database.

        The returned dictionary has the following keys:

        - ``total_articles`` (int): Total number of stored articles.
        - ``date_range`` (dict | None): ``{"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}``
          representing the earliest and latest article dates, or ``None`` if
          the database is empty.
        - ``course_count`` (int): Number of articles marked as courses.
        - ``top_tags`` (list[dict]): Up to 10 dicts of the form
          ``{"tag": str, "count": int}``, sorted by count descending.

        Returns:
            Statistics dictionary.  On error, returns zeroed-out values.
        """
        logger.info("Computing database statistics")
        try:
            # Total articles
            row = self._conn.execute("SELECT COUNT(*) FROM summaries").fetchone()
            total_articles: int = row[0] if row else 0

            # Date range
            date_range = None
            if total_articles > 0:
                row = self._conn.execute(
                    "SELECT MIN(date), MAX(date) FROM summaries"
                ).fetchone()
                if row and row[0] is not None:
                    date_range = {"from": row[0], "to": row[1]}

            # Course count
            row = self._conn.execute(
                "SELECT COUNT(*) FROM summaries WHERE is_course = 1"
            ).fetchone()
            course_count: int = row[0] if row else 0

            # Top tags — fetch all tags and count in Python for SQLite compat
            cursor = self._conn.execute("SELECT tags FROM summaries WHERE tags IS NOT NULL")
            tag_counter: Counter = Counter()
            for (tags_json,) in cursor.fetchall():
                try:
                    tags: list[str] = json.loads(tags_json) if tags_json else []
                    for tag in tags:
                        if tag:
                            tag_counter[tag] += 1
                except (json.JSONDecodeError, TypeError):
                    pass

            top_tags = [
                {"tag": tag, "count": count}
                for tag, count in tag_counter.most_common(10)
            ]

            stats = {
                "total_articles": total_articles,
                "date_range": date_range,
                "course_count": course_count,
                "top_tags": top_tags,
            }
            logger.info(
                "Stats: %d articles, %d courses, %d unique tags",
                total_articles,
                course_count,
                len(tag_counter),
            )
            return stats
        except Exception as exc:
            logger.error("get_stats failed: %s", exc)
            return {
                "total_articles": 0,
                "date_range": None,
                "course_count": 0,
                "top_tags": [],
            }

    def save_rating(self, url: str, rating: int) -> None:
        """Save a user rating for an article.

        Args:
            url: The article URL being rated.
            rating: ``+1`` for good / liked, ``-1`` for not relevant.
        """
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO feedback (url, rating) VALUES (?, ?)",
                    (url, rating),
                )
            logger.info("Saved rating=%+d for %s", rating, url)
        except Exception as exc:
            logger.error("save_rating failed for '%s': %s", url, exc)

    def get_feedback_summary(self) -> dict:
        """Return aggregated feedback: liked/disliked domains and tags.

        Joins the ``feedback`` table with ``summaries`` on URL, then
        aggregates net ratings per source domain and per tag.

        Returns:
            Dictionary with four keys:

            - ``liked_sources`` (list[str]): Source domains with net rating >= 2.
            - ``disliked_sources`` (list[str]): Source domains with net rating <= -2.
            - ``liked_tags`` (list[str]): Tags from positively rated articles.
            - ``disliked_tags`` (list[str]): Tags from negatively rated articles.

            All lists are empty when there is no feedback data.
        """
        try:
            # Aggregate net rating per source
            cursor = self._conn.execute(
                """
                SELECT s.source, SUM(f.rating) AS net
                FROM feedback f
                JOIN summaries s ON s.url = f.url
                WHERE s.source IS NOT NULL
                GROUP BY s.source
                """
            )
            liked_sources: list[str] = []
            disliked_sources: list[str] = []
            for row in cursor.fetchall():
                source, net = row[0], row[1]
                if net >= 2:
                    liked_sources.append(source)
                elif net <= -2:
                    disliked_sources.append(source)

            # Collect tags from positively and negatively rated articles
            cursor = self._conn.execute(
                """
                SELECT s.tags, SUM(f.rating) AS net
                FROM feedback f
                JOIN summaries s ON s.url = f.url
                WHERE s.tags IS NOT NULL
                GROUP BY s.url
                """
            )
            liked_tag_counter: Counter = Counter()
            disliked_tag_counter: Counter = Counter()
            for row in cursor.fetchall():
                tags_json, net = row[0], row[1]
                try:
                    tags: list[str] = json.loads(tags_json) if tags_json else []
                except (json.JSONDecodeError, TypeError):
                    tags = []
                for tag in tags:
                    if tag:
                        if net > 0:
                            liked_tag_counter[tag] += 1
                        elif net < 0:
                            disliked_tag_counter[tag] += 1

            liked_tags = [tag for tag, _ in liked_tag_counter.most_common()]
            disliked_tags = [tag for tag, _ in disliked_tag_counter.most_common()]

            logger.info(
                "Feedback summary: %d liked sources, %d disliked sources, "
                "%d liked tags, %d disliked tags",
                len(liked_sources),
                len(disliked_sources),
                len(liked_tags),
                len(disliked_tags),
            )
            return {
                "liked_sources": liked_sources,
                "disliked_sources": disliked_sources,
                "liked_tags": liked_tags,
                "disliked_tags": disliked_tags,
            }
        except Exception as exc:
            logger.error("get_feedback_summary failed: %s", exc)
            return {
                "liked_sources": [],
                "disliked_sources": [],
                "liked_tags": [],
                "disliked_tags": [],
            }

    def close(self) -> None:
        """Close the underlying SQLite connection.

        Calling this method is optional — the connection will be closed when
        the object is garbage-collected.  It is provided for callers that need
        explicit resource management (e.g. in tests or context managers).
        """
        try:
            self._conn.close()
            logger.debug("Knowledge database connection closed")
        except Exception as exc:
            logger.error("Error closing database connection: %s", exc)
