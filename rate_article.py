"""CLI script for saving article ratings to the NewsBrewer knowledge database.

Called by the GitHub Actions ``rate_article.yml`` workflow when a rating issue
is opened.  Can also be run locally for testing.

Usage::

    python rate_article.py --url "https://example.com/article" --rating good
    python rate_article.py --url "https://example.com/article" --rating bad
"""

import argparse
import sys

from src.knowledge.database import KnowledgeDatabase


def main() -> None:
    """Parse arguments and save the rating to the database."""
    parser = argparse.ArgumentParser(
        description="Save an article rating (+1 or -1) to the NewsBrewer knowledge database."
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Full URL of the article being rated.",
    )
    parser.add_argument(
        "--rating",
        required=True,
        choices=["good", "bad"],
        help="Rating: 'good' saves +1, 'bad' saves -1.",
    )

    args = parser.parse_args()

    rating_value = 1 if args.rating == "good" else -1

    db = KnowledgeDatabase("data/knowledge_base.db")
    db.save_rating(url=args.url, rating=rating_value)

    label = "Goed (+1)" if rating_value == 1 else "Niet relevant (-1)"
    print(f"Rating saved: {label}")
    print(f"URL: {args.url}")


if __name__ == "__main__":
    main()
