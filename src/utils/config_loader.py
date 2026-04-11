"""Configuration loader for the NewsBrewer pipeline.

Loads ``config.yml`` (structure/preferences) and ``.env`` (secrets) then
merges them into a single validated :class:`Config` dataclass.  Environment
variables always take precedence over placeholder values in ``config.yml`` so
that the example config file can ship with empty credential fields.

Typical usage::

    from src.utils.config_loader import load_config

    config = load_config()
    for account in config.email_sources:
        print(account.name, account.imap_server)

Environment variables consumed
-------------------------------
ACCOUNT_1_EMAIL, ACCOUNT_1_PASSWORD   — credentials for account index 0
ACCOUNT_2_EMAIL, ACCOUNT_2_PASSWORD   — credentials for account index 1
SMTP_EMAIL, SMTP_PASSWORD             — SMTP sender credentials
DIGEST_RECIPIENT                      — recipient address for the digest
GITHUB_TOKEN                          — LLM API token (GitHub Models / OpenAI)
"""

import os
import sys
from dataclasses import dataclass, field

import yaml
from dotenv import load_dotenv

from src.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AccountConfig:
    """IMAP account configuration for one email address.

    Attributes:
        name: Human-readable label (e.g. "My Gmail").
        imap_server: Hostname of the IMAP server.
        imap_port: Port number (typically 993 for IMAPS).
        email: Full email address for authentication.
        app_password: App-specific password or regular password.
    """

    name: str
    imap_server: str
    imap_port: int
    email: str
    app_password: str


@dataclass
class AIFocusConfig:
    """User preferences that guide article scoring and filtering.

    Attributes:
        language: ISO 639-1 language code for preferred article language.
        interesting_topics: Topics that earn high relevance scores.
        not_interesting_topics: Topics that earn low relevance scores.
        min_relevance_score: Articles scoring below this are excluded.
        highlight_courses: When True, course/tutorial items get a visual
            highlight in the digest email.
    """

    language: str
    interesting_topics: list[str]
    not_interesting_topics: list[str]
    min_relevance_score: float
    highlight_courses: bool


@dataclass
class DeliveryConfig:
    """SMTP and scheduling settings for digest delivery.

    Attributes:
        smtp_server: Hostname of the outgoing mail server.
        smtp_port: Port number (587 for STARTTLS, 465 for SSL).
        smtp_email: From address used when sending the digest.
        smtp_password: Password or app password for the SMTP account.
        send_to: Recipient email address for the digest.
        max_articles: Maximum number of articles to include per digest.
        schedule: Cron expression controlling automatic delivery timing.
        timezone: IANA timezone string used to interpret the cron schedule.
    """

    smtp_server: str
    smtp_port: int
    smtp_email: str
    smtp_password: str
    send_to: str
    max_articles: int
    schedule: str
    timezone: str
    github_repo: str = ""


@dataclass
class ModelConfig:
    """LLM provider and model selection.

    Attributes:
        provider: Provider identifier (e.g. "github_models", "openai").
        name: Model name recognised by the provider (e.g. "gpt-4o-mini").
    """

    provider: str
    name: str


@dataclass
class Config:
    """Top-level configuration object for the NewsBrewer pipeline.

    This dataclass is the single source of truth for all runtime settings.
    It is populated by :func:`load_config` and passed through the pipeline.

    Attributes:
        email_sources: List of IMAP accounts to poll for newsletters.
        newsletter_senders: Allowed sender addresses — only emails from these
            senders are processed during automated runs.
        manual_keyword: Subject-line keyword that triggers a manual digest
            (e.g. "BREW" matches subjects starting with "BREW:").
        ai_focus: Relevance scoring preferences.
        delivery: SMTP and scheduling settings.
        model: LLM provider and model to use for summarisation.
        github_token: API token for the configured LLM provider.
    """

    email_sources: list[AccountConfig]
    newsletter_senders: list[str]
    manual_keyword: str
    ai_focus: AIFocusConfig
    delivery: DeliveryConfig
    model: ModelConfig
    github_token: str
    github_repo: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_project_root() -> str:
    """Walk upward from this file to find the project root directory.

    The root is identified by the presence of ``requirements.txt``.

    Returns:
        Absolute path of the project root directory.

    Raises:
        FileNotFoundError: If the project root cannot be determined.
    """
    current = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        if os.path.isfile(os.path.join(current, "requirements.txt")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    raise FileNotFoundError(
        "Cannot locate the project root (directory containing requirements.txt). "
        "Make sure you are running NewsBrewer from within the project tree."
    )


def _load_yaml(config_path: str) -> dict:
    """Read and parse a YAML config file.

    Args:
        config_path: Absolute path to the YAML file.

    Returns:
        Parsed YAML content as a plain Python dictionary.

    Raises:
        SystemExit: With a clear message if the file is missing or malformed.
    """
    if not os.path.isfile(config_path):
        logger.error(
            "Configuration file not found: %s\n"
            "  -> Copy config.example.yml to config.yml and fill in your settings.",
            config_path,
        )
        sys.exit(1)

    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        logger.error(
            "Failed to parse '%s': %s\n"
            "  -> Check for syntax errors (indentation, colons, quotes).",
            config_path,
            exc,
        )
        sys.exit(1)

    if not isinstance(data, dict):
        logger.error(
            "'%s' is empty or does not contain a YAML mapping at the top level.",
            config_path,
        )
        sys.exit(1)

    logger.info("Loaded configuration from '%s'", config_path)
    return data


def _require_env(var_name: str, description: str) -> str:
    """Fetch a required environment variable, exiting with a helpful message if absent.

    Args:
        var_name: Name of the environment variable.
        description: Human-readable description used in the error message.

    Returns:
        The value of the environment variable as a string.

    Raises:
        SystemExit: If the variable is not set or is empty.
    """
    value = os.environ.get(var_name, "").strip()
    if not value:
        logger.error(
            "Required environment variable '%s' is not set (%s).\n"
            "  -> Add it to your .env file or export it in your shell.",
            var_name,
            description,
        )
        sys.exit(1)
    return value


def _optional_env(var_name: str, default: str = "") -> str:
    """Fetch an optional environment variable, returning *default* if absent.

    Args:
        var_name: Name of the environment variable.
        default: Value to return when the variable is unset or empty.

    Returns:
        The environment variable value, or *default*.
    """
    return os.environ.get(var_name, "").strip() or default


def _parse_accounts(raw_accounts: list[dict]) -> list[AccountConfig]:
    """Parse the ``email_sources.accounts`` section and inject env-var credentials.

    The first account's credentials are read from ``ACCOUNT_1_EMAIL`` /
    ``ACCOUNT_1_PASSWORD``; the second from ``ACCOUNT_2_EMAIL`` /
    ``ACCOUNT_2_PASSWORD``; and so on up to index 9.  If an env var is not
    set the placeholder value from config.yml is used — validation later
    will catch empty values for the first account.

    Args:
        raw_accounts: List of account dicts from the parsed YAML.

    Returns:
        List of :class:`AccountConfig` instances.
    """
    accounts: list[AccountConfig] = []
    for idx, raw in enumerate(raw_accounts):
        account_num = idx + 1
        email_var = f"ACCOUNT_{account_num}_EMAIL"
        pass_var = f"ACCOUNT_{account_num}_PASSWORD"

        email = _optional_env(email_var) or raw.get("email", "")
        password = _optional_env(pass_var) or raw.get("app_password", "")

        name = raw.get("name", f"Account {account_num}")
        imap_server = raw.get("imap_server", "")
        imap_port = int(raw.get("imap_port", 993))

        if not imap_server:
            logger.error(
                "Account '%s' (index %d in config.yml) is missing 'imap_server'.",
                name,
                idx,
            )
            sys.exit(1)

        if not email:
            logger.warning(
                "Account '%s': email not set via config.yml or %s. "
                "This account will likely fail to authenticate.",
                name,
                email_var,
            )

        accounts.append(
            AccountConfig(
                name=name,
                imap_server=imap_server,
                imap_port=imap_port,
                email=email,
                app_password=password,
            )
        )
        logger.info("Registered account: %s (%s)", name, imap_server)

    return accounts


def _parse_ai_focus(raw: dict) -> AIFocusConfig:
    """Parse the ``ai_focus`` section of the YAML configuration.

    Args:
        raw: The ``ai_focus`` sub-dictionary from the parsed YAML.

    Returns:
        A populated :class:`AIFocusConfig` instance.
    """
    return AIFocusConfig(
        language=raw.get("language", "en"),
        interesting_topics=raw.get("interesting_topics", []),
        not_interesting_topics=raw.get("not_interesting_topics", []),
        min_relevance_score=float(raw.get("min_relevance_score", 6.0)),
        highlight_courses=bool(raw.get("highlight_courses", True)),
    )


def _parse_delivery(raw: dict) -> DeliveryConfig:
    """Parse the ``delivery`` section and inject SMTP credentials from env vars.

    Args:
        raw: The ``delivery`` sub-dictionary from the parsed YAML.

    Returns:
        A populated :class:`DeliveryConfig` instance.
    """
    smtp_email = _optional_env("SMTP_EMAIL") or raw.get("smtp_email", "")
    smtp_password = _optional_env("SMTP_PASSWORD") or raw.get("smtp_password", "")
    send_to = _optional_env("DIGEST_RECIPIENT") or raw.get("send_to", "")
    github_repo = _optional_env("GITHUB_REPO") or raw.get("github_repo", "")

    return DeliveryConfig(
        smtp_server=raw.get("smtp_server", "smtp.gmail.com"),
        smtp_port=int(raw.get("smtp_port", 587)),
        smtp_email=smtp_email,
        smtp_password=smtp_password,
        send_to=send_to,
        max_articles=int(raw.get("max_articles", 10)),
        schedule=raw.get("schedule", "0 6 * * *"),
        timezone=raw.get("timezone", "UTC"),
        github_repo=github_repo,
    )


def _parse_model(raw: dict) -> ModelConfig:
    """Parse the ``model`` section of the YAML configuration.

    Args:
        raw: The ``model`` sub-dictionary from the parsed YAML.

    Returns:
        A populated :class:`ModelConfig` instance.
    """
    return ModelConfig(
        provider=raw.get("provider", "github_models"),
        name=raw.get("name", "gpt-4o-mini"),
    )


def _validate_config(config: Config) -> None:
    """Run post-parse validation and exit with clear messages on failure.

    Checks that at least one account is configured with credentials and that
    SMTP delivery settings are present.

    Args:
        config: The assembled :class:`Config` to validate.

    Raises:
        SystemExit: On the first validation error encountered.
    """
    if not config.email_sources:
        logger.error(
            "No email accounts configured in config.yml under 'email_sources.accounts'."
        )
        sys.exit(1)

    first = config.email_sources[0]
    if not first.email:
        logger.error(
            "The first email account ('%s') has no email address. "
            "Set ACCOUNT_1_EMAIL in your .env file.",
            first.name,
        )
        sys.exit(1)
    if not first.app_password:
        logger.error(
            "The first email account ('%s') has no password. "
            "Set ACCOUNT_1_PASSWORD in your .env file.",
            first.name,
        )
        sys.exit(1)

    if not config.delivery.smtp_email:
        logger.error(
            "SMTP sender address is not configured. "
            "Set SMTP_EMAIL in your .env file or config.yml."
        )
        sys.exit(1)

    if not config.delivery.smtp_password:
        logger.error(
            "SMTP password is not configured. "
            "Set SMTP_PASSWORD in your .env file."
        )
        sys.exit(1)

    if not config.delivery.send_to:
        logger.error(
            "Digest recipient is not configured. "
            "Set DIGEST_RECIPIENT in your .env file or config.yml."
        )
        sys.exit(1)

    if not config.github_token:
        logger.error(
            "GITHUB_TOKEN is not set. "
            "In GitHub Actions it is injected automatically; "
            "for local runs add it to your .env file."
        )
        sys.exit(1)

    logger.info("Configuration validated successfully.")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config(config_path: str | None = None, env_path: str | None = None) -> Config:
    """Load, merge, validate, and return the NewsBrewer :class:`Config`.

    This is the sole entry point for configuration loading.  Call it once at
    pipeline startup and pass the resulting object through all stages.

    Args:
        config_path: Absolute path to ``config.yml``.  Defaults to
            ``<project_root>/config.yml`` if not supplied.
        env_path: Absolute path to the ``.env`` file.  Defaults to
            ``<project_root>/.env`` if not supplied.

    Returns:
        A fully populated and validated :class:`Config` instance.

    Raises:
        SystemExit: On any configuration error, with a descriptive message
            so the user knows exactly what to fix.

    Example::

        config = load_config()
        print(config.delivery.send_to)
    """
    logger.info("Loading NewsBrewer configuration...")

    # Resolve default paths from the project root.
    try:
        root = _resolve_project_root()
    except FileNotFoundError as exc:
        logger.error(str(exc))
        sys.exit(1)

    if config_path is None:
        config_path = os.path.join(root, "config.yml")
    if env_path is None:
        env_path = os.path.join(root, ".env")

    # Load .env first so environment variables are available for YAML merging.
    if os.path.isfile(env_path):
        load_dotenv(dotenv_path=env_path, override=False)
        logger.info("Loaded secrets from '%s'", env_path)
    else:
        logger.warning(
            ".env file not found at '%s'. "
            "Relying on environment variables already present in the shell.",
            env_path,
        )

    # Parse YAML.
    raw = _load_yaml(config_path)

    # Parse each section.
    email_sources_raw = raw.get("email_sources", {})
    raw_accounts: list[dict] = email_sources_raw.get("accounts", [])
    newsletter_senders: list[str] = email_sources_raw.get("newsletter_senders", [])
    manual_keyword: str = email_sources_raw.get("manual_keyword", "BREW")

    accounts = _parse_accounts(raw_accounts)
    ai_focus = _parse_ai_focus(raw.get("ai_focus", {}))
    delivery = _parse_delivery(raw.get("delivery", {}))
    model = _parse_model(raw.get("model", {}))
    github_token = _optional_env("GITHUB_TOKEN")

    config = Config(
        email_sources=accounts,
        newsletter_senders=newsletter_senders,
        manual_keyword=manual_keyword,
        ai_focus=ai_focus,
        delivery=delivery,
        model=model,
        github_token=github_token,
        github_repo=delivery.github_repo,
    )

    _validate_config(config)
    logger.info(
        "Configuration ready — %d account(s), %d newsletter sender(s), "
        "model=%s/%s, recipient=%s",
        len(config.email_sources),
        len(config.newsletter_senders),
        config.model.provider,
        config.model.name,
        config.delivery.send_to,
    )
    return config
