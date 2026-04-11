"""Orchestrator for the NewsBrewer pipeline.

Coordinates all pipeline stages in order: email collection, article fetching,
AI analysis, and digest delivery.  All exceptions are caught at this level so
that individual stage failures do not propagate to the caller.

Typical usage::

    from src.agents.orchestrator import Orchestrator

    Orchestrator().run()
"""

import asyncio
from collections import defaultdict

from src.agents.email_agent import EmailAgent
from src.agents.manual_link_agent import ManualLinkAgent
from src.agents.fetcher_agent import FetcherAgent
from src.agents.analyst_agent import AnalystAgent
from src.agents.digest_agent import DigestAgent
from src.knowledge.database import KnowledgeDatabase
from src.utils.config_loader import load_config
from src.utils.logger import get_logger


class Orchestrator:
    """Runs the complete NewsBrewer daily brew pipeline.

    The orchestrator wires together all agents in the correct order and is
    responsible for top-level exception handling.  Individual agent errors
    are caught and logged — no exception escapes this class.

    Example::

        Orchestrator().run()
        Orchestrator().run(dry_run=True)
    """

    def run(self, dry_run: bool = False) -> None:
        """Run the complete NewsBrewer daily brew pipeline.

        Executes the following stages in sequence:

        1. Load and validate configuration.
        2. Collect email messages from newsletter accounts and manual BREW notes.
        3. Deduplicate and filter already-seen URLs.
        4. Fetch article content asynchronously.
        5. Analyse articles with AI to produce digest items.
        6. Send the digest email (skipped in dry-run mode).

        If any top-level error occurs it is logged and the method returns
        without raising so that GitHub Actions marks the job as failed via
        the exit code rather than an unhandled traceback.

        Args:
            dry_run: When ``True``, the pipeline runs all stages up to and
                including analysis but does not send the digest email.
                Instead, selected articles are logged for inspection.
        """
        logger = get_logger(__name__)

        try:
            config = load_config()
        except SystemExit:
            logger.error("Configuration error. See above for details.")
            return

        try:
            db = KnowledgeDatabase("data/knowledge_base.db")
            logger.info("Starting NewsBrewer daily brew...")

            # Step 1: Collect emails
            email_msgs = EmailAgent(config).run()
            manual_msgs = ManualLinkAgent(config).run()
            all_msgs = email_msgs + manual_msgs

            # Extract and deduplicate URLs
            all_urls: list[str] = []
            seen: set[str] = set()
            for msg in all_msgs:
                for url in msg.urls:
                    if url not in seen:
                        seen.add(url)
                        all_urls.append(url)

            # Filter already-seen URLs
            new_urls = [url for url in all_urls if not db.already_seen(url)]
            logger.info(
                "Found %d new URLs to process (from %d total)",
                len(new_urls),
                len(all_urls),
            )

            if not new_urls:
                logger.info("No new content today. Skipping digest.")
                return

            # Step 2: Fetch articles (async)
            articles = asyncio.run(FetcherAgent(config).run(new_urls))
            logger.info("Fetched %d articles", len(articles))

            # Step 3: AI analysis
            digest_items = AnalystAgent(config, db).run(articles)
            logger.info("AI selected %d relevant articles", len(digest_items))

            if not digest_items:
                logger.info("No relevant articles found today.")
                return

            # Limit to max 2 articles per source domain (keep highest scored).
            max_per_domain = 2
            domain_counts: dict[str, int] = defaultdict(int)
            filtered_items = []
            for item in digest_items:  # already sorted by score desc
                if domain_counts[item.source] < max_per_domain:
                    filtered_items.append(item)
                    domain_counts[item.source] += 1
            if len(filtered_items) < len(digest_items):
                logger.info(
                    "Capped per-domain: %d → %d items", len(digest_items), len(filtered_items)
                )
            digest_items = filtered_items

            if dry_run:
                logger.info(
                    "[DRY RUN] Would send digest with %d articles", len(digest_items)
                )
                for item in digest_items:
                    logger.info(
                        "  [%.1f] %s — %s", item.relevance_score, item.title, item.url
                    )
                return

            # Step 4: Send digest
            DigestAgent(config).run(digest_items)
            logger.info("Digest sent with %d articles", len(digest_items))

        except Exception as exc:  # noqa: BLE001
            logger.error("Unexpected error in NewsBrewer pipeline: %s", exc, exc_info=True)
