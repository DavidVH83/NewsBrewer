"""NewsBrewer — Daily AI knowledge digest tool.

Run this script to execute the complete NewsBrewer pipeline: collect newsletter
emails, fetch and analyse article content with AI, then send a curated digest.

Usage::

    python main.py               # Run the daily brew
    python main.py --dry-run     # Test without sending email
"""

import argparse

from src.agents.orchestrator import Orchestrator


def main() -> None:
    """Entry point for NewsBrewer.

    Parses command-line arguments and delegates execution to the
    :class:`~src.agents.orchestrator.Orchestrator`.  Designed to be called
    either directly (``python main.py``) or via a scheduled GitHub Actions
    workflow.
    """
    parser = argparse.ArgumentParser(
        description="NewsBrewer — Brew your daily AI knowledge digest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py               Run the daily brew
  python main.py --dry-run     Test without sending email
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline but don't send email (for testing)",
    )
    args = parser.parse_args()

    Orchestrator().run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
