"""Analyst agent for the NewsBrewer pipeline.

Uses an AI provider to evaluate fetched articles against the user's configured
interests, producing :class:`~src.models.digest_item.DigestItem` objects for
articles that pass the relevance threshold.  Relevant items are persisted to
the knowledge database and returned sorted by relevance score descending.

Usage::

    from src.agents.analyst_agent import AnalystAgent

    agent = AnalystAgent(config=config, db=db)
    digest_items = agent.run(articles)
"""

import json
import re
from datetime import datetime
from urllib.parse import urlparse

from src.knowledge.database import KnowledgeDatabase
from src.models.article import Article
from src.models.digest_item import DigestItem
from src.providers.github_models import GitHubModelsProvider
from src.utils.config_loader import Config
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
You are a personal AI knowledge curator.
Your job: analyze articles and decide what is valuable for someone learning AI development.

User's interests: {interesting_topics}
Not interesting: {not_interesting_topics}

For each article respond ONLY with valid JSON in this exact format:
{{
  "relevant": true/false,
  "relevance_score": 0.0-10.0,
  "summary": "2-3 sentence summary in {language}. Focus on what is new and actionable.",
  "is_course": true/false,
  "tags": ["tag1", "tag2"]
}}

Relevance scoring:
- 8-10: Must read, directly applicable, new technique or tool
- 6-7: Worth reading, good background knowledge
- 4-5: Marginally relevant, borderline
- 0-3: Not relevant, marketing, generic content

Only include articles with relevance_score >= {min_score}\
"""

_USER_PROMPT_TEMPLATE = """\
Title: {title}
Source: {source}
Content: {text}\
"""

_TITLE_ONLY_PROMPT_TEMPLATE = """\
Title: {title}
Source: {source}
Note: Full article content could not be fetched (paywall/protection). Judge relevance based on the title alone.\
"""


def _title_from_url(url: str) -> str:
    """Extract a human-readable title from a URL slug.

    Converts URL path segments like
    ``building-a-scalable-production-grade-agentic-rag-pipeline-1168dcd36260``
    into ``Building A Scalable Production Grade Agentic Rag Pipeline``.
    Falls back to the raw URL if no slug can be extracted.

    Args:
        url: Full URL string.

    Returns:
        Title-cased string derived from the URL path, or the raw URL.
    """
    try:
        path = urlparse(url).path.rstrip("/")
        slug = path.split("/")[-1]
        # Remove trailing hex IDs (e.g. -1168dcd36260)
        slug = re.sub(r"-[0-9a-f]{10,}$", "", slug)
        # Replace hyphens with spaces and title-case
        return slug.replace("-", " ").title()
    except Exception:
        return url


class AnalystAgent:
    """AI-powered agent that scores and summarises fetched articles.

    For each article that was successfully fetched, the agent sends the title
    and body text to an LLM and asks it to rate relevance against the user's
    configured interests.  Articles that score at or above the configured
    minimum relevance threshold are converted to :class:`DigestItem` objects,
    saved to the knowledge database, and returned to the caller.

    Args:
        config: Pipeline configuration providing AI focus settings, model
            name, and GitHub token.
        db: Knowledge database used to persist accepted digest items.
    """

    def __init__(self, config: Config, db: KnowledgeDatabase) -> None:
        """Initialise the analyst agent.

        Args:
            config: Pipeline configuration object.
            db: Knowledge database for persisting accepted digest items.
        """
        self._config = config
        self._db = db
        self._feedback_addendum: str = ""
        self._provider = GitHubModelsProvider(
            token=config.github_token,
            model=config.model.name,
        )

    def _build_feedback_addendum(self) -> str:
        """Build a feedback addendum for the system prompt from past ratings.

        Queries the database for accumulated ratings.  Returns an empty string
        when fewer than 3 ratings exist so as not to distort early results.

        Returns:
            A multi-line string to append to the system prompt, or ``""`` when
            there is insufficient feedback data.
        """
        try:
            cursor = self._db._conn.execute("SELECT COUNT(*) FROM feedback")
            total_ratings: int = cursor.fetchone()[0]
        except Exception:
            return ""

        if total_ratings < 3:
            return ""

        summary = self._db.get_feedback_summary()
        lines: list[str] = ["\nBased on past feedback:"]

        if summary["liked_sources"]:
            lines.append(
                f"- Articles from these sources were rated positively: "
                f"{', '.join(summary['liked_sources'])}"
            )
        if summary["liked_tags"]:
            lines.append(
                f"- These topic tags were well received: "
                f"{', '.join(summary['liked_tags'])}"
            )
        if summary["disliked_sources"]:
            lines.append(
                f"- Articles from these sources were rated negatively: "
                f"{', '.join(summary['disliked_sources'])}"
            )
        if summary["disliked_tags"]:
            lines.append(
                f"- These topic areas were not interesting: "
                f"{', '.join(summary['disliked_tags'])}"
            )

        if len(lines) == 1:
            # Only the header — no meaningful data
            return ""

        return "\n".join(lines)

    def run(self, articles: list[Article]) -> list[DigestItem]:
        """Analyze articles with AI, return relevant DigestItems sorted by score desc.

        Iterates over the supplied articles, skipping any whose fetch status is
        ``"failed"``.  Each remaining article is analysed individually; those
        that meet the relevance threshold are saved to the database.

        Args:
            articles: List of fetched :class:`Article` objects to evaluate.

        Returns:
            List of :class:`DigestItem` objects for relevant articles, sorted
            by :attr:`~DigestItem.relevance_score` in descending order.
        """
        self._feedback_addendum = self._build_feedback_addendum()
        if self._feedback_addendum:
            logger.info("Feedback addendum loaded (%d chars)", len(self._feedback_addendum))

        results: list[DigestItem] = []

        for article in articles:
            if article.fetch_status == "failed":
                logger.debug("Skipping failed article: %s", article.url)
                continue

            digest_item = self._analyze_article(article)
            if digest_item is not None:
                self._db.insert(digest_item)
                results.append(digest_item)

        results.sort(key=lambda item: item.relevance_score, reverse=True)
        logger.info(
            "AnalystAgent processed %d article(s), %d accepted",
            len(articles),
            len(results),
        )
        return results

    def _analyze_article(self, article: Article) -> DigestItem | None:
        """Analyze a single article. Returns DigestItem if relevant, None if not.

        Builds the system and user prompts from the pipeline configuration and
        article content, then delegates to the AI provider for a completion.

        Args:
            article: The article to evaluate.

        Returns:
            A :class:`DigestItem` if the article meets the relevance threshold,
            otherwise ``None``.
        """
        ai_focus = self._config.ai_focus

        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            interesting_topics=", ".join(ai_focus.interesting_topics),
            not_interesting_topics=", ".join(ai_focus.not_interesting_topics),
            language=ai_focus.language,
            min_score=ai_focus.min_relevance_score,
        ) + getattr(self, "_feedback_addendum", "")

        # When content is thin (e.g. Medium/Cloudflare paywall), derive title
        # from the URL slug and instruct AI to judge on title alone.
        title_override: str | None = None
        if article.fetch_status == "partial" and article.word_count < 100:
            derived_title = _title_from_url(article.url)
            if derived_title == article.url or len(derived_title) < 10:
                # URL slug not useful (e.g. profile pages like /@username) — skip
                logger.debug("Skipping profile/non-article URL: %s", article.url)
                return None
            title_override = derived_title
            user_prompt = _TITLE_ONLY_PROMPT_TEMPLATE.format(
                title=derived_title,
                source=article.source_domain,
            )
            logger.debug("Title-only analysis: %s → '%s'", article.url, derived_title)
        else:
            user_prompt = _USER_PROMPT_TEMPLATE.format(
                title=article.title,
                source=article.source_domain,
                text=article.full_text,
            )

        response = self._provider.complete(system=system_prompt, user=user_prompt)

        if not response:
            logger.warning(
                "Empty response from AI provider for article: %s", article.url
            )
            return None

        return self._parse_ai_response(response, article, title_override=title_override)

    def _parse_ai_response(self, response: str, article: Article, title_override: str | None = None) -> DigestItem | None:
        """Parse JSON response from AI. Returns None on parse failure.

        Strips optional Markdown code fences (````json ... ````), then parses
        the JSON payload.  Validates that the ``relevant`` flag is ``True`` and
        that ``relevance_score`` meets the configured minimum before
        constructing a :class:`DigestItem`.

        Args:
            response: Raw string returned by the AI provider.
            article: The source article, used to populate URL, title, and
                source fields on the resulting :class:`DigestItem`.

        Returns:
            A :class:`DigestItem` if the response is valid, the article is
            relevant, and the score meets the threshold; otherwise ``None``.
        """
        try:
            # Strip Markdown code fences if present (```json ... ``` or ``` ... ```)
            cleaned = re.sub(
                r"```(?:json)?\s*(.*?)\s*```",
                r"\1",
                response,
                flags=re.DOTALL,
            ).strip()

            parsed: dict = json.loads(cleaned)

            # Validate relevance flag and minimum score.
            if not parsed.get("relevant", False):
                logger.debug(
                    "Article marked not relevant by AI: %s", article.url
                )
                return None

            relevance_score: float = float(parsed.get("relevance_score", 0.0))
            min_score: float = self._config.ai_focus.min_relevance_score

            if relevance_score < min_score:
                logger.debug(
                    "Article score %.1f below threshold %.1f: %s",
                    relevance_score,
                    min_score,
                    article.url,
                )
                return None

            # Use title_override (URL-derived) when article.title is a Cloudflare/paywall placeholder
            effective_title = title_override or parsed.get("title") or article.title

            return DigestItem(
                url=article.url,
                title=effective_title,
                source=article.source_domain,
                summary=parsed["summary"],
                is_course=bool(parsed.get("is_course", False)),
                relevance_score=relevance_score,
                tags=list(parsed.get("tags", [])),
                date=datetime.now().date(),
            )

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error(
                "Failed to parse AI response for article '%s': %s | response=%r",
                article.url,
                exc,
                response,
            )
            return None
