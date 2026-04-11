"""Terminal display helpers for NewsBrewer knowledge-base search results.

This module provides pure formatting functions used by the ``search.py`` CLI
tool to render search results and database statistics in a readable,
colour-friendly way without requiring any third-party terminal libraries.

Usage::

    from src.knowledge.search import format_result, format_stats

    for i, result in enumerate(results, start=1):
        print(format_result(result, i))

    print(format_stats(stats))
"""

from src.utils.logger import get_logger

logger = get_logger(__name__)


def format_result(result: dict, index: int) -> str:
    """Format a single search result for terminal display.

    Produces a compact, human-readable block that shows the title, source,
    date, relevance score, tags, a short summary, and the URL.

    Example output::

        1. 📄 Understanding RAG Pipelines
           Source: towardsdatascience.com | Date: 2026-04-09 | Score: 8.5
           Tags: RAG, LLM, retrieval
           Retrieval-Augmented Generation (RAG) combines dense retrieval with
           generative models to ground outputs in external knowledge sources…
           → https://towardsdatascience.com/understanding-rag

    Args:
        result: A result dictionary as returned by
            :meth:`~src.knowledge.database.KnowledgeDatabase.search` or
            similar methods.
        index: 1-based position of this result in the overall list, used as
            a visual prefix.

    Returns:
        A formatted multi-line string ready to be printed to stdout.
    """
    try:
        title = result.get("title") or "Untitled"
        source = result.get("source") or "Unknown source"
        date_val = result.get("date") or "Unknown date"
        score = result.get("relevance_score")
        score_str = f"{score:.1f}" if score is not None else "N/A"
        tags = result.get("tags") or []
        tags_str = ", ".join(tags) if tags else "—"
        summary = result.get("summary") or ""
        url = result.get("url") or ""

        lines = [
            f"{index}. \U0001f4c4 {title}",
            f"   Source: {source} | Date: {date_val} | Score: {score_str}",
            f"   Tags: {tags_str}",
        ]
        if summary:
            lines.append(f"   {summary}")
        if url:
            lines.append(f"   \u2192 {url}")

        return "\n".join(lines)
    except Exception as exc:
        logger.error("format_result failed for index %d: %s", index, exc)
        return f"{index}. [Error formatting result]"


def format_stats(stats: dict) -> str:
    """Format database statistics for terminal display.

    Produces a multi-line summary of the knowledge-base stats returned by
    :meth:`~src.knowledge.database.KnowledgeDatabase.get_stats`.

    Example output::

        \U0001f4ca Knowledge Base Statistics
        ──────────────────────────────
        Total articles : 142
        Date range     : 2026-01-01  →  2026-04-09
        Courses        : 17
        Top tags       : LLM (34), RAG (28), Agents (19), ...

    Args:
        stats: A statistics dictionary as returned by
            :meth:`~src.knowledge.database.KnowledgeDatabase.get_stats`.

    Returns:
        A formatted multi-line string ready to be printed to stdout.
    """
    try:
        total = stats.get("total_articles", 0)
        course_count = stats.get("course_count", 0)
        date_range = stats.get("date_range")
        top_tags: list[dict] = stats.get("top_tags") or []

        divider = "\u2500" * 34

        if date_range:
            date_str = f"{date_range['from']}  \u2192  {date_range['to']}"
        else:
            date_str = "No data"

        tags_str = (
            ", ".join(f"{t['tag']} ({t['count']})" for t in top_tags)
            if top_tags
            else "—"
        )

        lines = [
            "\U0001f4ca Knowledge Base Statistics",
            divider,
            f"Total articles : {total}",
            f"Date range     : {date_str}",
            f"Courses        : {course_count}",
            f"Top tags       : {tags_str}",
        ]
        return "\n".join(lines)
    except Exception as exc:
        logger.error("format_stats failed: %s", exc)
        return "[Error formatting stats]"
