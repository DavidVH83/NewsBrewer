"""NewsBrewer CLI search tool for the local knowledge base.

Provides a simple command-line interface for querying the SQLite knowledge
base built by the NewsBrewer pipeline.

Usage examples::

    # Full-text search
    python search.py "RAG retrieval"

    # Show all courses
    python search.py --courses

    # Courses added in the last 30 days
    python search.py --courses --days 30

    # Database statistics
    python search.py --stats

    # Limit number of results
    python search.py "LangGraph" --limit 5
"""

import argparse
import io
import sys
from datetime import date, timedelta

from src.knowledge.database import KnowledgeDatabase
from src.knowledge.search import format_result, format_stats
from src.utils.logger import get_logger

logger = get_logger(__name__)

_DIVIDER = "\u2500" * 50


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for the search CLI.

    Returns:
        A configured :class:`argparse.ArgumentParser` instance.
    """
    parser = argparse.ArgumentParser(
        prog="search.py",
        description="Search the NewsBrewer knowledge base.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Search query string (FTS5 syntax supported).",
    )
    parser.add_argument(
        "--courses",
        action="store_true",
        help="Show course / learning-resource entries.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        metavar="N",
        help="When used with --courses, filter to entries from the last N days.",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show knowledge-base statistics.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="Maximum number of results to display (default: 10).",
    )
    parser.add_argument(
        "--db",
        default="data/knowledge_base.db",
        metavar="PATH",
        help="Path to the SQLite database file (default: data/knowledge_base.db).",
    )
    return parser


def _print_results(results: list[dict], header: str) -> None:
    """Print a list of formatted results with a header and divider.

    Args:
        results: List of result dicts from the knowledge database.
        header: Header line to display above the results.
    """
    print(header)
    print(_DIVIDER)
    if not results:
        print("No results found.")
        return
    for i, result in enumerate(results, start=1):
        print(format_result(result, i))
        if i < len(results):
            print()


def main() -> None:
    """Entry point for the NewsBrewer search CLI.

    Parses command-line arguments and dispatches to the appropriate
    :class:`~src.knowledge.database.KnowledgeDatabase` method.  Output is
    written to stdout.  All errors are caught, logged, and reported to the
    user without a traceback.
    """
    # Ensure stdout can handle Unicode/emoji on all platforms (including
    # Windows consoles that default to a narrow code-page).
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    parser = _build_parser()
    args = parser.parse_args()

    # Validate: at least one mode must be selected.
    if not args.query and not args.courses and not args.stats:
        parser.print_help()
        sys.exit(0)

    try:
        db = KnowledgeDatabase(db_path=args.db)
    except Exception as exc:
        print(f"Error: Could not open database — {exc}", file=sys.stderr)
        logger.error("Failed to open database: %s", exc)
        sys.exit(1)

    try:
        # --- stats mode ---
        if args.stats:
            stats = db.get_stats()
            print(format_stats(stats))
            return

        # --- courses mode ---
        if args.courses:
            results = db.get_courses(limit=args.limit)

            # Optionally filter by date window.
            if args.days is not None:
                cutoff = date.today() - timedelta(days=args.days)
                results = [
                    r for r in results
                    if r.get("date") and str(r["date"]) >= str(cutoff)
                ]

            if args.days is not None:
                header = (
                    f"\U0001f393 Courses from the last {args.days} day(s) "
                    f"({len(results)} found)"
                )
            else:
                header = f"\U0001f393 Courses ({len(results)} found)"

            _print_results(results, header)
            return

        # --- full-text search mode ---
        if args.query:
            results = db.search(args.query, limit=args.limit)
            header = f'\U0001f50d Results for "{args.query}" ({len(results)} found)'
            _print_results(results, header)

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        logger.error("Unhandled error in search CLI: %s", exc)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
