"""HTML email builder for the NewsBrewer digest.

Renders the Jinja2 ``templates/digest_email.html`` template with a list of
:class:`~src.models.digest_item.DigestItem` objects and returns a ready-to-send
HTML string.

Typical usage::

    from src.utils.html_builder import build_digest_html

    html = build_digest_html(items)
    # Pass html to DigestAgent._send_email(...)
"""

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from src.models.digest_item import DigestItem
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Path resolution — walk up from this file to the project root, then into templates/.
_THIS_FILE = Path(__file__).resolve()
_PROJECT_ROOT = _THIS_FILE.parents[2]  # src/utils -> src -> project root
_TEMPLATES_DIR = _PROJECT_ROOT / "templates"
_TEMPLATE_NAME = "digest_email.html"


def build_digest_html(
    items: list[DigestItem],
    digest_date: date | None = None,
    github_repo: str = "",
    narrative_html: str = "",
) -> str:
    """Build an HTML email string from a list of DigestItems.

    Loads ``templates/digest_email.html`` relative to the project root,
    splits *items* into courses and regular articles, formats the date, and
    renders the Jinja2 template.

    Args:
        items: All :class:`~src.models.digest_item.DigestItem` objects to
            include in the digest — both courses and regular articles.
        digest_date: The date to display in the email header and subject.
            Defaults to today's date if not provided.
        github_repo: GitHub repository in ``owner/repo`` format (e.g.
            ``davidvanham83/newsbrewer``).  When non-empty, rating links are
            rendered on each article card.  Defaults to ``""`` (no links).
        narrative_html: Pre-rendered HTML narrative string.  When non-empty,
            the template shows the flowing narrative and a compact article
            index instead of individual cards.  Defaults to ``""`` (card
            layout fallback).

    Returns:
        Rendered HTML string ready to be attached to an email.  Returns an
        empty string if the template cannot be loaded or rendering fails.
    """
    if digest_date is None:
        digest_date = date.today()

    # Format: "Wednesday, April 9, 2026"
    # Use %d for the day then strip the leading zero manually so it works on
    # both Linux (which supports %-d) and Windows (which does not).
    date_str: str = digest_date.strftime("%A, %B {day}, %Y").format(
        day=digest_date.day
    )

    courses: list[DigestItem] = [item for item in items if item.is_course]
    articles: list[DigestItem] = [item for item in items if not item.is_course]
    article_count: int = len(items)

    try:
        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=True,
        )
        template = env.get_template(_TEMPLATE_NAME)
    except TemplateNotFound:
        logger.error(
            "Digest template not found: %s (looked in %s)",
            _TEMPLATE_NAME,
            _TEMPLATES_DIR,
        )
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to load Jinja2 environment: %s", exc)
        return ""

    try:
        html: str = template.render(
            date_str=date_str,
            article_count=article_count,
            courses=courses,
            articles=articles,
            all_items=items,
            github_repo=github_repo,
            narrative_html=narrative_html,
        )
        logger.info(
            "Rendered digest HTML — %d article(s) (%d course(s)) for %s",
            article_count,
            len(courses),
            date_str,
        )
        return html
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to render digest template: %s", exc)
        return ""
