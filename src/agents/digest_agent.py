"""Digest sending agent for the NewsBrewer pipeline.

:class:`DigestAgent` builds an HTML email from a list of
:class:`~src.models.digest_item.DigestItem` objects and delivers it via SMTP
using the credentials and settings from :class:`~src.utils.config_loader.Config`.

Typical usage::

    from src.agents.digest_agent import DigestAgent

    agent = DigestAgent(config)
    agent.run(items)
"""

import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.models.digest_item import DigestItem
from src.providers.github_models import GitHubModelsProvider
from src.utils.config_loader import Config
from src.utils.html_builder import build_digest_html
from src.utils.logger import get_logger


class DigestAgent:
    """Builds and sends the NewsBrewer digest email.

    This agent is the final stage of the pipeline.  It receives ranked
    :class:`~src.models.digest_item.DigestItem` objects, renders them into
    the HTML template, composes an SMTP message, and delivers it to the
    configured recipient.

    Attributes:
        config: The top-level :class:`~src.utils.config_loader.Config` instance
            providing SMTP credentials and delivery settings.
        logger: Module-level logger for this agent.
    """

    def __init__(self, config: Config) -> None:
        """Initialise the DigestAgent with pipeline configuration.

        Args:
            config: Fully populated :class:`~src.utils.config_loader.Config`
                instance.  The ``config.delivery`` sub-config must contain
                valid SMTP credentials and a recipient address.
        """
        self.config = config
        self.logger = get_logger(__name__)

    def run(self, items: list[DigestItem]) -> None:
        """Build and send the digest email for today's items.

        Renders the HTML template, constructs the subject line, and calls
        :meth:`_send_email`.  If *items* is empty, a digest is still sent so
        the recipient knows the pipeline ran but found nothing new.

        Args:
            items: Ranked list of :class:`~src.models.digest_item.DigestItem`
                objects to include in today's digest.
        """
        today = date.today()
        self.logger.info(
            "DigestAgent.run — building digest for %s with %d item(s)",
            today.isoformat(),
            len(items),
        )

        # Generate one flowing narrative text from all digest items.
        narrative_html = ""
        if items:
            try:
                provider = GitHubModelsProvider(
                    token=self.config.github_token,
                    model=self.config.model.name,
                )
                narrative_html = provider.generate_narrative(
                    items,
                    language=self.config.ai_focus.language,
                )
                if narrative_html:
                    self.logger.info("Narrative generated (%d chars)", len(narrative_html))
                else:
                    self.logger.warning(
                        "Narrative generation returned empty — falling back to card layout"
                    )
            except Exception as exc:  # noqa: BLE001
                self.logger.error("Narrative generation error: %s — falling back", exc)

        html = build_digest_html(
            items,
            digest_date=today,
            github_repo=getattr(self.config, "github_repo", ""),
            narrative_html=narrative_html,
        )
        if not html:
            self.logger.error(
                "HTML rendering produced an empty string — aborting send."
            )
            return

        subject = self._build_subject(items, today)
        self._send_email(html, subject)

    def _build_subject(self, items: list[DigestItem], today: date) -> str:
        """Construct the email subject line.

        Format: ``⚗️ NewsBrewer | Wednesday, April 9 · 5 articles``

        Args:
            items: The list of digest items — used only for the count.
            today: The date shown in the subject line.

        Returns:
            Formatted subject string.
        """
        # Format: "Wednesday, April 9" (no year in subject to keep it concise).
        # Avoid %-d which is Linux-only; strip the leading zero manually instead.
        date_part = today.strftime("%A, %B {day}").format(day=today.day)
        count = len(items)
        article_word = "article" if count == 1 else "articles"
        return f"⚗️ NewsBrewer | {date_part} · {count} {article_word}"

    def _send_email(self, html: str, subject: str) -> None:
        """Send the rendered HTML digest via SMTP with STARTTLS.

        Connects to the configured SMTP server, upgrades the connection to
        TLS with ``STARTTLS``, authenticates, and delivers the message.  Any
        exception is caught and logged so the pipeline does not crash.

        Args:
            html: Fully rendered HTML string to use as the email body.
            subject: Email subject line.
        """
        delivery = self.config.delivery

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = delivery.smtp_email
        msg["To"] = delivery.send_to
        msg.attach(MIMEText(html, "html"))

        self.logger.info(
            "Sending digest — server=%s:%d  from=%s  to=%s",
            delivery.smtp_server,
            delivery.smtp_port,
            delivery.smtp_email,
            delivery.send_to,
        )

        try:
            with smtplib.SMTP(delivery.smtp_server, delivery.smtp_port) as server:
                server.starttls()
                server.login(delivery.smtp_email, delivery.smtp_password)
                server.sendmail(
                    delivery.smtp_email,
                    [delivery.send_to],
                    msg.as_string(),
                )
            self.logger.info("Digest sent successfully to %s", delivery.send_to)
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Failed to send digest email to %s: %s",
                delivery.send_to,
                exc,
            )
