"""GitHub Models AI provider for the NewsBrewer pipeline.

Wraps the OpenAI-compatible GitHub Models inference endpoint so that the rest
of the pipeline can request chat completions without knowing the underlying API
details.

Usage::

    from src.providers.github_models import GitHubModelsProvider

    provider = GitHubModelsProvider(token="ghp_...", model="gpt-4o-mini")
    reply = provider.complete(system="You are helpful.", user="Summarise this.")
"""

from openai import OpenAI

from src.utils.logger import get_logger

logger = get_logger(__name__)


class GitHubModelsProvider:
    """Thin wrapper around the GitHub Models (OpenAI-compatible) inference API.

    GitHub Models exposes an OpenAI-compatible REST endpoint at
    ``https://models.inference.ai.azure.com``.  This class configures an
    :class:`openai.OpenAI` client to point at that endpoint and provides a
    single :meth:`complete` method for chat completions.

    Args:
        token: GitHub personal access token (or ``GITHUB_TOKEN`` in Actions)
            used as the API key.
        model: Model identifier supported by the GitHub Models marketplace.
            Defaults to ``"gpt-4o-mini"``.
    """

    def __init__(self, token: str, model: str = "gpt-4o-mini") -> None:
        """Initialize with GitHub Models endpoint.

        Args:
            token: GitHub personal access token used for authentication.
            model: Model name to use for completions (e.g. ``"gpt-4o-mini"``).
        """
        self.client = OpenAI(
            base_url="https://models.inference.ai.azure.com",
            api_key=token,
        )
        self.model = model

    def complete(self, system: str, user: str, max_tokens: int = 500) -> str:
        """Send a chat completion request and return the response text.

        Constructs a two-message conversation (system + user) and calls the
        GitHub Models inference endpoint.  Uses a low temperature (0.3) so
        that responses are consistent and deterministic.

        Args:
            system: System-role message that sets the assistant's behaviour
                and output format.
            user: User-role message containing the content to process.
            max_tokens: Maximum number of tokens the model may generate.
                Defaults to 500.

        Returns:
            The assistant's reply as a plain string.  Returns an empty string
            if any exception occurs during the API call.
        """
        import time

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        for attempt in (1, 2):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=max_tokens,
                )
                content = response.choices[0].message.content
                if content:
                    return content
                # Empty response — retry once after a short wait.
                if attempt == 1:
                    logger.warning("Empty response from API (attempt %d) — retrying in 3s", attempt)
                    time.sleep(3)
                else:
                    logger.warning("Empty response from API after retry — giving up")
                    return ""
            except Exception as exc:
                if attempt == 1:
                    logger.warning("GitHub Models API call failed (attempt %d): %s — retrying in 3s", attempt, exc)
                    time.sleep(3)
                else:
                    logger.error("GitHub Models API call failed after retry: %s", exc)
                    return ""
        return ""

    def generate_narrative(self, digest_items: list, language: str = "nl") -> str:
        """Generate one flowing narrative text from multiple digest items.

        Takes all selected articles and produces a single cohesive text
        with [[keyword|url]] markers converted to clickable HTML links.

        Args:
            digest_items: List of :class:`~src.models.digest_item.DigestItem`
                objects to weave into the narrative.
            language: Language code for the narrative (e.g. ``"nl"`` for Dutch).
                Defaults to ``"nl"``.

        Returns:
            HTML string with clickable ``<a>`` links for key terms.  Returns an
            empty string if the API call fails or no items are provided.
        """
        if not digest_items:
            return ""

        system_prompt = (
            f"You are writing a daily AI newsletter in {language}.\n"
            "Write ONE flowing text of 400-600 words that covers all the articles below.\n"
            "The text should read as a coherent narrative, not as separate summaries.\n\n"
            "Mark 2-3 key terms per article using this EXACT format: [[term|url]]\n"
            "where term is the clickable word/phrase and url is the EXACT article URL from the input.\n"
            "Only mark genuinely important technical terms (model names, technique names, tool names).\n"
            "Do NOT mark generic words like \"AI\" or \"article\".\n"
            "IMPORTANT: Always close every marker with ]]. Never leave a marker unclosed.\n\n"
            "Output ONLY the narrative text with [[term|url]] markers. No headers, no bullets."
        )

        articles_block = "\n\n".join(
            f"TITLE: {item.title}\nURL: {item.url}\nSUMMARY: {item.summary}"
            for item in digest_items
        )
        user_prompt = f"Articles to cover:\n{articles_block}"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=1500,
            )
            content = response.choices[0].message.content
            if not content:
                return ""
            return self._markers_to_html(content)
        except Exception as exc:
            logger.error("Narrative generation failed: %s", exc)
            return ""

    @staticmethod
    def _markers_to_html(text: str) -> str:
        """Convert [[term|url]] markers to <a href> links.

        Also cleans up any unclosed markers (e.g. truncated by token limit)
        by replacing them with just the term text.

        Args:
            text: Raw narrative text containing ``[[term|url]]`` markers.

        Returns:
            Text with markers replaced by styled HTML anchor elements.
        """
        import re

        # Convert well-formed [[term|url]] markers to links.
        result = re.sub(
            r"\[\[([^\]|]+)\|([^\]]*)\]\]",
            r'<a href="\2" style="color:#e94560;text-decoration:none;font-weight:bold;">\1</a>',
            text,
            flags=re.DOTALL,
        )
        # Remove any leftover unclosed [[term|url or [[term markers (truncated by token limit).
        result = re.sub(r"\[\[([^\]|]*)\|[^\]]*$", r"\1", result, flags=re.DOTALL)
        result = re.sub(r"\[\[([^\]]*)\]\]?", r"\1", result)
        return result
