"""NewsBrewer Interactive Setup Wizard.

Guides the user through configuring NewsBrewer end-to-end:

  * Email accounts (Gmail / Outlook / Yahoo / iCloud / custom)
  * Newsletter senders to monitor
  * AI focus topics and relevance preferences
  * Digest delivery address and schedule
  * Optional GitHub repository for pushing secrets

Generates ``config.yml`` and ``.env`` in the project root, then
optionally uploads secrets to GitHub Actions via the ``gh`` CLI.

Usage::

    python wizard.py            # Run the full interactive wizard
    python wizard.py --help     # Show usage information
    python wizard.py --check    # Validate existing config.yml and .env
"""

from __future__ import annotations

import imaplib
import os
import re
import shutil
import subprocess
import sys
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_PASSWORD_LINKS: dict[str, str] = {
    "gmail": "https://myaccount.google.com/apppasswords",
    "outlook": (
        "https://account.microsoft.com/security "
        "(Advanced security -> App passwords)"
    ),
    "yahoo": (
        "https://login.yahoo.com/account/security "
        "(Generate app password)"
    ),
    "icloud": (
        "https://appleid.apple.com "
        "(Sign-In and Security -> App-Specific Passwords)"
    ),
    "custom": (
        "Check your email host documentation for IMAP and app password settings"
    ),
}

IMAP_SERVERS: dict[str, tuple[str, int] | None] = {
    "gmail": ("imap.gmail.com", 993),
    "outlook": ("outlook.office365.com", 993),
    "yahoo": ("imap.mail.yahoo.com", 993),
    "icloud": ("imap.mail.me.com", 993),
    "custom": None,  # Ask user
}

SMTP_SERVERS: dict[str, tuple[str, int]] = {
    "gmail": ("smtp.gmail.com", 587),
    "outlook": ("smtp.office365.com", 587),
    "yahoo": ("smtp.mail.yahoo.com", 587),
    "icloud": ("smtp.mail.me.com", 587),
    "custom": ("smtp.gmail.com", 587),
}

DEFAULT_NEWSLETTER_SENDERS: list[str] = [
    "noreply@medium.com",
    "hello@agenticengineering.com",
    "noreply@anthropic.com",
    "support@datacamp.com",
    "newsletter@tldr.tech",
    "hello@bensbites.com",
    "digest@hackernewsletter.com",
]

DEFAULT_INTERESTING_TOPICS: list[str] = [
    "AI agents and agentic systems",
    "LLMs, RAG, fine-tuning, prompt engineering",
    "New AI developer tools and frameworks",
    "AI courses and learning resources",
    "MCP, context engineering, AI infrastructure",
]

DEFAULT_NOT_INTERESTING_TOPICS: list[str] = [
    "Marketing without technical depth",
    "No-code website builders",
    "Company stock prices and earnings",
]

# GitHub Actions secrets that need to be pushed
GITHUB_SECRETS: list[str] = [
    "ACCOUNT_1_EMAIL",
    "ACCOUNT_1_PASSWORD",
    "ACCOUNT_2_EMAIL",
    "ACCOUNT_2_PASSWORD",
    "SMTP_EMAIL",
    "SMTP_PASSWORD",
    "DIGEST_RECIPIENT",
]

EMAIL_REGEX: re.Pattern[str] = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)

# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------


def print_banner() -> None:
    """Print the NewsBrewer welcome banner."""
    print()
    print("=" * 60)
    print("   NewsBrewer Setup Wizard")
    print("=" * 60)
    print("This wizard will create config.yml and .env for you.")
    print("You can re-run it at any time to update your settings.")
    print()


def print_section(title: str) -> None:
    """Print a formatted section header.

    Args:
        title: The section title to display.
    """
    print(f"\n--- {title} ---")


def prompt(message: str, default: str = "") -> str:
    """Display a prompt and return the user's input, falling back to default.

    Args:
        message: The prompt text shown to the user.
        default: Value returned (and shown) when the user presses Enter.

    Returns:
        The user's input string, or *default* if the user pressed Enter.
    """
    if default:
        display = f"{message} [{default}]: "
    else:
        display = f"{message}: "
    value = input(display).strip()
    return value if value else default


def prompt_required(message: str, validator: Any = None) -> str:
    """Keep prompting until the user provides a non-empty value.

    Args:
        message: The prompt text shown to the user.
        validator: Optional callable that returns True when the value is
            acceptable.  If it returns False, an error is shown and the
            prompt repeats.

    Returns:
        A validated, non-empty string entered by the user.
    """
    while True:
        value = input(f"{message}: ").strip()
        if not value:
            print("  This field is required. Please enter a value.")
            continue
        if validator is not None and not validator(value):
            print("  Invalid value. Please try again.")
            continue
        return value


def prompt_yes_no(message: str, default: bool = True) -> bool:
    """Ask a yes/no question and return a boolean.

    Args:
        message: The question to display.
        default: The value returned when the user presses Enter.

    Returns:
        True if the user answered yes, False otherwise.
    """
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"{message} {hint}: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  Please enter y or n.")


def is_valid_email(value: str) -> bool:
    """Return True when *value* looks like a valid email address.

    Uses a simple regex — not RFC 5321 compliant, but good enough for setup.

    Args:
        value: The string to validate.

    Returns:
        True if the string matches a basic email pattern.
    """
    return bool(EMAIL_REGEX.match(value))


# ---------------------------------------------------------------------------
# Step 1: Email provider selection
# ---------------------------------------------------------------------------


def step_select_providers() -> list[str]:
    """Ask the user which email providers they want to configure.

    Displays a numbered menu; the user types a comma-separated list of
    numbers (e.g. ``1,3``).  At least one provider must be selected.

    Returns:
        List of selected provider keys (e.g. ``["gmail", "outlook"]``).
    """
    print_section("Step 1: Email Providers")
    providers = list(IMAP_SERVERS.keys())
    print("Which email providers do you use for newsletters?")
    print("(You can select multiple by entering comma-separated numbers)\n")
    for i, name in enumerate(providers, 1):
        print(f"  {i}. {name.capitalize()}")
    print()

    while True:
        raw = input("Enter numbers (e.g. 1 or 1,2): ").strip()
        if not raw:
            print("  Please select at least one provider.")
            continue
        selected: list[str] = []
        valid = True
        for part in raw.split(","):
            part = part.strip()
            if not part.isdigit() or not (1 <= int(part) <= len(providers)):
                print(f"  '{part}' is not a valid choice. Enter numbers 1-{len(providers)}.")
                valid = False
                break
            key = providers[int(part) - 1]
            if key not in selected:
                selected.append(key)
        if valid and selected:
            return selected


# ---------------------------------------------------------------------------
# Step 2: Per-account configuration
# ---------------------------------------------------------------------------


def test_imap_connection(
    server: str, port: int, email: str, password: str
) -> bool:
    """Attempt an IMAP SSL login and return True on success.

    Never raises — all exceptions are caught and reported as a failed test.

    Args:
        server: IMAP server hostname.
        port: IMAP server port (usually 993).
        email: Email address to authenticate with.
        password: App password or account password.

    Returns:
        True if the login succeeded, False otherwise.
    """
    print("  Testing connection...", end=" ", flush=True)
    try:
        mail = imaplib.IMAP4_SSL(host=server, port=port)
        mail.login(email, password)
        mail.logout()
        print("connected successfully")
        return True
    except imaplib.IMAP4.error as exc:
        print(f"failed (authentication error: {exc})")
        return False
    except OSError as exc:
        print(f"failed (network error: {exc})")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"failed ({exc})")
        return False


def step_configure_account(
    provider: str, account_index: int
) -> dict[str, Any]:
    """Collect IMAP credentials for one email account.

    Shows the app-password help link for the provider, then prompts for
    email address and app password, then tests the IMAP connection.

    Args:
        provider: Provider key (e.g. ``"gmail"``).
        account_index: 1-based index used for labelling (Account 1, 2, …).

    Returns:
        Dictionary with keys ``name``, ``imap_server``, ``imap_port``,
        ``email``, and ``app_password``.  Also includes ``env_email_key``
        and ``env_pass_key`` for writing ``.env``.
    """
    print_section(f"Step 2: {provider.capitalize()} Account (Account {account_index})")

    # Show app-password link
    link = APP_PASSWORD_LINKS[provider]
    print(f"  App password link: {link}\n")

    # Determine IMAP server
    imap_info = IMAP_SERVERS[provider]
    if imap_info is None:
        # Custom provider — ask for server details
        imap_server = prompt_required("  IMAP server hostname (e.g. imap.example.com)")
        while True:
            port_str = prompt("  IMAP port", default="993")
            if port_str.isdigit():
                imap_port = int(port_str)
                break
            print("  Please enter a valid port number.")
        display_name = prompt("  Account label", default="My Custom Email")
    else:
        imap_server, imap_port = imap_info
        display_name = prompt(
            "  Account label",
            default=f"My {provider.capitalize()}",
        )

    # Email address
    while True:
        email_addr = input(f"  Email address for {provider}: ").strip()
        if not email_addr:
            print("  Email address is required.")
            continue
        if not is_valid_email(email_addr):
            print("  That doesn't look like a valid email address. Try again.")
            continue
        break

    # App password (hide from terminal where possible)
    import getpass  # noqa: PLC0415 — local import to avoid cluttering top-level
    app_password = ""
    while not app_password:
        try:
            app_password = getpass.getpass(f"  App password for {email_addr}: ").strip()
        except (EOFError, OSError):
            # Fallback when getpass isn't available (e.g. piped input)
            app_password = input(f"  App password for {email_addr}: ").strip()
        if not app_password:
            print("  App password is required.")

    # Test the connection
    connection_ok = test_imap_connection(imap_server, imap_port, email_addr, app_password)
    if not connection_ok:
        print()
        continue_anyway = prompt_yes_no(
            "  Connection test failed. Continue anyway?", default=False
        )
        if not continue_anyway:
            print("  Skipping this account. You can re-run setup later.")
            return {}  # caller will skip empty dicts

    env_key = account_index  # 1-based
    return {
        "name": display_name,
        "imap_server": imap_server,
        "imap_port": imap_port,
        "email": email_addr,
        "app_password": app_password,
        "env_email_key": f"ACCOUNT_{env_key}_EMAIL",
        "env_pass_key": f"ACCOUNT_{env_key}_PASSWORD",
        "provider": provider,
    }


# ---------------------------------------------------------------------------
# Step 3: Newsletter senders
# ---------------------------------------------------------------------------


def step_newsletter_senders() -> list[str]:
    """Let the user choose which newsletter senders to monitor.

    Shows a numbered list of defaults; the user can confirm all, deselect
    some, and add their own custom addresses.

    Returns:
        Final list of sender email addresses to monitor.
    """
    print_section("Step 3: Newsletter Senders")
    print("These are senders whose emails NewsBrewer will read and summarise.\n")
    print("Default senders:")
    for i, sender in enumerate(DEFAULT_NEWSLETTER_SENDERS, 1):
        print(f"  {i}. {sender}")
    print()

    use_defaults = prompt_yes_no("Keep all default senders?", default=True)
    if use_defaults:
        senders = list(DEFAULT_NEWSLETTER_SENDERS)
    else:
        senders = []
        print("Enter the numbers you want to keep (comma-separated), or press Enter to skip all:")
        raw = input("  Keep senders: ").strip()
        if raw:
            for part in raw.split(","):
                part = part.strip()
                if part.isdigit() and 1 <= int(part) <= len(DEFAULT_NEWSLETTER_SENDERS):
                    addr = DEFAULT_NEWSLETTER_SENDERS[int(part) - 1]
                    if addr not in senders:
                        senders.append(addr)

    # Add custom senders
    print()
    print("Add your own newsletter senders (one per line, blank line to finish):")
    while True:
        raw = input("  Sender email (or Enter to finish): ").strip()
        if not raw:
            break
        if is_valid_email(raw):
            if raw not in senders:
                senders.append(raw)
                print(f"  Added: {raw}")
            else:
                print(f"  Already in list: {raw}")
        else:
            print("  That doesn't look like a valid email address.")

    print(f"\n  Monitoring {len(senders)} sender(s).")
    return senders


# ---------------------------------------------------------------------------
# Step 4: AI focus topics
# ---------------------------------------------------------------------------


def step_ai_focus() -> dict[str, Any]:
    """Collect AI focus preferences: interesting topics, not-interesting topics, threshold.

    Pre-fills with project defaults; the user can accept them or customise.

    Returns:
        Dictionary suitable for the ``ai_focus`` section of ``config.yml``.
    """
    print_section("Step 4: AI Focus Topics")
    print("NewsBrewer scores each article for relevance.")
    print("The AI uses your topics to decide what's worth including.\n")

    # Interesting topics
    print("Default INTERESTING topics:")
    for i, t in enumerate(DEFAULT_INTERESTING_TOPICS, 1):
        print(f"  {i}. {t}")
    keep_interesting = prompt_yes_no("\nKeep these interesting topics?", default=True)
    if keep_interesting:
        interesting = list(DEFAULT_INTERESTING_TOPICS)
    else:
        interesting = _edit_topic_list("interesting", DEFAULT_INTERESTING_TOPICS)

    print("\nAdd more interesting topics (blank line to finish):")
    while True:
        raw = input("  Topic (or Enter to finish): ").strip()
        if not raw:
            break
        if raw not in interesting:
            interesting.append(raw)
            print(f"  Added: {raw}")

    # Not-interesting topics
    print()
    print("Default NOT INTERESTING topics:")
    for i, t in enumerate(DEFAULT_NOT_INTERESTING_TOPICS, 1):
        print(f"  {i}. {t}")
    keep_not = prompt_yes_no("\nKeep these not-interesting topics?", default=True)
    if keep_not:
        not_interesting = list(DEFAULT_NOT_INTERESTING_TOPICS)
    else:
        not_interesting = _edit_topic_list("not-interesting", DEFAULT_NOT_INTERESTING_TOPICS)

    print("\nAdd more not-interesting topics (blank line to finish):")
    while True:
        raw = input("  Topic (or Enter to finish): ").strip()
        if not raw:
            break
        if raw not in not_interesting:
            not_interesting.append(raw)
            print(f"  Added: {raw}")

    # Minimum relevance score
    while True:
        score_str = prompt("\nMinimum relevance score (1-10)", default="6.0")
        try:
            score = float(score_str)
            if 1.0 <= score <= 10.0:
                break
            print("  Score must be between 1.0 and 10.0.")
        except ValueError:
            print("  Please enter a number (e.g. 6.0).")

    # Language
    language = prompt("Content language code (en, fr, de, …)", default="en")

    # Highlight courses
    highlight = prompt_yes_no("Highlight courses and tutorials in digest?", default=True)

    return {
        "language": language,
        "interesting_topics": interesting,
        "not_interesting_topics": not_interesting,
        "min_relevance_score": score,
        "highlight_courses": highlight,
    }


def _edit_topic_list(label: str, defaults: list[str]) -> list[str]:
    """Interactively keep or drop topics from a default list.

    Args:
        label: Human-readable label used in prompts (e.g. ``"interesting"``).
        defaults: The default list of topics to present.

    Returns:
        The subset of *defaults* that the user chose to keep.
    """
    print(f"\nEnter numbers to KEEP from the {label} list (comma-separated):")
    raw = input("  Keep: ").strip()
    kept: list[str] = []
    if raw:
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= len(defaults):
                topic = defaults[int(part) - 1]
                if topic not in kept:
                    kept.append(topic)
    return kept


# ---------------------------------------------------------------------------
# Step 5: Delivery settings
# ---------------------------------------------------------------------------


def step_delivery(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    """Collect digest delivery settings: recipient, schedule, SMTP credentials.

    If the user configured a Gmail account, the wizard offers to reuse it as
    the SMTP sender so they don't have to enter credentials twice.

    Args:
        accounts: List of configured account dicts from :func:`step_configure_account`.

    Returns:
        Dictionary suitable for the ``delivery`` section of ``config.yml``,
        plus extra keys ``smtp_email_value`` and ``smtp_password_value``
        for writing to ``.env``.
    """
    print_section("Step 5: Delivery Settings")

    # Recipient
    while True:
        recipient = input("  Digest recipient email address: ").strip()
        if not recipient:
            print("  Recipient is required.")
            continue
        if not is_valid_email(recipient):
            print("  That doesn't look like a valid email address.")
            continue
        break

    # SMTP sender — try to reuse an already-configured account
    smtp_email_val = ""
    smtp_pass_val = ""
    smtp_server = "smtp.gmail.com"
    smtp_port = 587

    if accounts:
        print()
        print("  Available accounts for sending the digest:")
        for i, acc in enumerate(accounts, 1):
            print(f"    {i}. {acc['email']} ({acc['provider']})")
        print(f"    {len(accounts) + 1}. Enter different SMTP credentials")
        print()
        while True:
            choice_str = prompt(
                f"  Use which account for sending? (1-{len(accounts) + 1})",
                default="1",
            )
            if choice_str.isdigit() and 1 <= int(choice_str) <= len(accounts) + 1:
                choice = int(choice_str)
                break
            print(f"  Please enter a number between 1 and {len(accounts) + 1}.")

        if choice <= len(accounts):
            chosen = accounts[choice - 1]
            smtp_email_val = chosen["email"]
            smtp_pass_val = chosen["app_password"]
            provider_key = chosen["provider"]
            if provider_key in SMTP_SERVERS:
                smtp_server, smtp_port = SMTP_SERVERS[provider_key]
            print(f"  Using {smtp_email_val} as SMTP sender.")
        # else: fall through to manual entry

    if not smtp_email_val:
        # Manual SMTP entry
        import getpass  # noqa: PLC0415

        while True:
            smtp_email_val = input("  SMTP sender email address: ").strip()
            if smtp_email_val and is_valid_email(smtp_email_val):
                break
            print("  Please enter a valid email address.")

        try:
            smtp_pass_val = getpass.getpass("  SMTP app password: ").strip()
        except (EOFError, OSError):
            smtp_pass_val = input("  SMTP app password: ").strip()

        smtp_server_input = prompt("  SMTP server", default="smtp.gmail.com")
        smtp_server = smtp_server_input if smtp_server_input else "smtp.gmail.com"
        while True:
            port_str = prompt("  SMTP port", default="587")
            if port_str.isdigit():
                smtp_port = int(port_str)
                break
            print("  Please enter a valid port number.")

    # Schedule
    print()
    print("  Delivery schedule (cron format, UTC).")
    print("  Examples:")
    print("    0 6 * * *   — every day at 06:00 UTC")
    print("    0 8 * * 1-5 — weekdays at 08:00 UTC")
    schedule = prompt("  Cron schedule", default="0 6 * * *")

    # Timezone
    timezone = prompt("  Timezone for schedule display", default="Europe/Brussels")

    # Max articles
    while True:
        max_str = prompt("  Maximum articles per digest", default="10")
        if max_str.isdigit() and int(max_str) > 0:
            max_articles = int(max_str)
            break
        print("  Please enter a positive integer.")

    return {
        "smtp_server": smtp_server,
        "smtp_port": smtp_port,
        "smtp_email": "",       # written via .env
        "smtp_password": "",    # written via .env
        "send_to": "",          # written via .env
        "max_articles": max_articles,
        "schedule": schedule,
        "timezone": timezone,
        # Extra keys consumed by file writers, not written to config.yml
        "smtp_email_value": smtp_email_val,
        "smtp_password_value": smtp_pass_val,
        "recipient_value": recipient,
    }


# ---------------------------------------------------------------------------
# Step 6: GitHub repo (optional)
# ---------------------------------------------------------------------------


def step_github() -> str:
    """Ask for an optional GitHub repository URL for pushing secrets.

    Returns:
        The GitHub repo URL string, or an empty string if the user skips.
    """
    print_section("Step 6: GitHub Repository (Optional)")
    print("  If you provide a GitHub repo URL, we can push your secrets")
    print("  to GitHub Actions secrets so your automated digest works.\n")
    print("  Format: https://github.com/username/repo")
    print("  (Press Enter to skip)\n")
    raw = input("  GitHub repo URL: ").strip()
    if raw and not raw.startswith("http"):
        raw = "https://github.com/" + raw.lstrip("/")
    return raw


# ---------------------------------------------------------------------------
# File generation
# ---------------------------------------------------------------------------


def build_config_yml(
    accounts: list[dict[str, Any]],
    senders: list[str],
    ai_focus: dict[str, Any],
    delivery: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the config.yml data structure.

    Args:
        accounts: Configured account dicts (from step 2).
        senders: Newsletter sender addresses (from step 3).
        ai_focus: AI focus config dict (from step 4).
        delivery: Delivery config dict (from step 5).

    Returns:
        A nested dictionary ready to be serialised with PyYAML.
    """
    account_entries: list[dict[str, Any]] = []
    for acc in accounts:
        if not acc:
            continue
        account_entries.append(
            {
                "name": acc["name"],
                "imap_server": acc["imap_server"],
                "imap_port": acc["imap_port"],
                "email": "",        # credentials live in .env
                "app_password": "", # credentials live in .env
            }
        )

    # Strip the extra keys that live only in .env
    delivery_yml = {k: v for k, v in delivery.items()
                    if k not in ("smtp_email_value", "smtp_password_value", "recipient_value")}

    return {
        "email_sources": {
            "accounts": account_entries,
            "newsletter_senders": senders,
            "manual_keyword": "BREW",
        },
        "ai_focus": ai_focus,
        "delivery": delivery_yml,
        "model": {
            "provider": "github_models",
            "name": "gpt-4o-mini",
        },
    }


def build_env_lines(
    accounts: list[dict[str, Any]],
    delivery: dict[str, Any],
) -> list[str]:
    """Build the content lines for the ``.env`` file.

    Args:
        accounts: Configured account dicts (from step 2).
        delivery: Delivery config dict (from step 5).

    Returns:
        List of strings (one per line) to write verbatim to ``.env``.
    """
    lines: list[str] = [
        "# Generated by NewsBrewer Setup Wizard — never commit this file\n",
        "\n",
    ]

    for acc in accounts:
        if not acc:
            continue
        env_email = acc["env_email_key"]
        env_pass = acc["env_pass_key"]
        lines.append(f"# {acc['name']}\n")
        lines.append(f"{env_email}={acc['email']}\n")
        lines.append(f"{env_pass}={acc['app_password']}\n")
        lines.append("\n")

    smtp_email = delivery.get("smtp_email_value", "")
    smtp_pass = delivery.get("smtp_password_value", "")
    recipient = delivery.get("recipient_value", "")

    lines.append("# SMTP credentials for sending the digest\n")
    lines.append(f"SMTP_EMAIL={smtp_email}\n")
    lines.append(f"SMTP_PASSWORD={smtp_pass}\n")
    lines.append(f"DIGEST_RECIPIENT={recipient}\n")
    lines.append("\n")
    lines.append("# GitHub Models / OpenAI API token\n")
    lines.append("# In GitHub Actions: automatically available as GITHUB_TOKEN\n")
    lines.append("# For local testing: create a GitHub personal access token\n")
    lines.append("GITHUB_TOKEN=\n")

    return lines


def write_config_yml(config_data: dict[str, Any], output_path: str) -> None:
    """Serialise *config_data* to YAML and write it to *output_path*.

    Args:
        config_data: The nested config dictionary to serialise.
        output_path: Absolute path where ``config.yml`` will be written.
    """
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write("# ============================================================\n")
        fh.write("# NEWSBREWER CONFIGURATION — generated by wizard.py\n")
        fh.write("# Do NOT commit this file — add config.yml to .gitignore\n")
        fh.write("# ============================================================\n\n")
        yaml.dump(
            config_data,
            fh,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
    print(f"  Written: {output_path}")


def write_env_file(env_lines: list[str], output_path: str) -> None:
    """Write ``.env`` lines to *output_path* using plain text.

    Args:
        env_lines: Lines to write, each ending with ``\\n``.
        output_path: Absolute path where ``.env`` will be written.
    """
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.writelines(env_lines)
    print(f"  Written: {output_path}")


# ---------------------------------------------------------------------------
# GitHub secrets push
# ---------------------------------------------------------------------------


def push_github_secrets(
    accounts: list[dict[str, Any]],
    delivery: dict[str, Any],
    repo_url: str,
) -> None:
    """Push configured secrets to GitHub Actions using the ``gh`` CLI.

    Checks that ``gh`` is available before attempting any calls.  Each
    secret is set individually via ``gh secret set``.  Errors are reported
    but do not abort the wizard.

    Args:
        accounts: Configured account dicts (from step 2).
        delivery: Delivery config dict including ``smtp_email_value`` etc.
        repo_url: GitHub repository URL (e.g. ``https://github.com/user/repo``).
    """
    if not shutil.which("gh"):
        print(
            "\n  'gh' CLI not found. Skipping GitHub secrets push.\n"
            "  Install from https://cli.github.com/ and run:\n"
            "    gh auth login\n"
            "  Then re-run: python wizard.py"
        )
        return

    print(f"\n  Pushing secrets to: {repo_url}")

    # Build the secrets dict
    secrets: dict[str, str] = {}
    for acc in accounts:
        if not acc:
            continue
        secrets[acc["env_email_key"]] = acc["email"]
        secrets[acc["env_pass_key"]] = acc["app_password"]

    secrets["SMTP_EMAIL"] = delivery.get("smtp_email_value", "")
    secrets["SMTP_PASSWORD"] = delivery.get("smtp_password_value", "")
    secrets["DIGEST_RECIPIENT"] = delivery.get("recipient_value", "")

    ok_count = 0
    fail_count = 0
    for name, value in secrets.items():
        if not value:
            print(f"    Skipping {name} (empty value)")
            continue
        result = subprocess.run(  # noqa: S603
            ["gh", "secret", "set", name, "--body", value, "--repo", repo_url],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            print(f"    Set {name}")
            ok_count += 1
        else:
            err = result.stderr.strip() or result.stdout.strip()
            print(f"    Failed to set {name}: {err}")
            fail_count += 1

    print(f"\n  Secrets pushed: {ok_count} succeeded, {fail_count} failed.")
    if fail_count:
        print("  Run 'gh auth status' to check your authentication.")


# ---------------------------------------------------------------------------
# --check mode: validate existing config
# ---------------------------------------------------------------------------


def check_existing_config(project_root: str) -> None:
    """Validate existing ``config.yml`` and ``.env`` files and report issues.

    Checks for missing required keys, empty credential placeholders, and
    reports the overall status.  Exits with code 0 on success, 1 on errors.

    Args:
        project_root: Absolute path to the project root directory.
    """
    config_path = os.path.join(project_root, "config.yml")
    env_path = os.path.join(project_root, ".env")

    print_banner()
    print("--- Checking Existing Configuration ---\n")

    errors: list[str] = []
    warnings: list[str] = []

    # Check config.yml
    if not os.path.isfile(config_path):
        errors.append(f"config.yml not found at {config_path}")
    else:
        try:
            with open(config_path, "r", encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh)
            if not isinstance(cfg, dict):
                errors.append("config.yml is empty or not a valid YAML mapping")
            else:
                # Check accounts
                accounts = cfg.get("email_sources", {}).get("accounts", [])
                if not accounts:
                    errors.append("No accounts defined under email_sources.accounts")
                else:
                    for i, acc in enumerate(accounts):
                        if not acc.get("imap_server"):
                            errors.append(f"Account {i+1}: missing imap_server")

                # Check AI focus
                ai = cfg.get("ai_focus", {})
                if not ai.get("interesting_topics"):
                    warnings.append("ai_focus.interesting_topics is empty")

                # Check delivery
                dlv = cfg.get("delivery", {})
                if not dlv.get("smtp_server"):
                    errors.append("delivery.smtp_server is missing")
                if not dlv.get("schedule"):
                    warnings.append("delivery.schedule is not set")

                print(f"  config.yml: OK ({len(accounts)} account(s) configured)")
        except yaml.YAMLError as exc:
            errors.append(f"config.yml has YAML syntax errors: {exc}")

    # Check .env
    if not os.path.isfile(env_path):
        errors.append(f".env not found at {env_path}")
    else:
        env_vars: dict[str, str] = {}
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env_vars[k.strip()] = v.strip()

        required_keys = ["ACCOUNT_1_EMAIL", "ACCOUNT_1_PASSWORD", "SMTP_EMAIL",
                         "SMTP_PASSWORD", "DIGEST_RECIPIENT"]
        for key in required_keys:
            if key not in env_vars:
                errors.append(f".env is missing {key}")
            elif not env_vars[key] or env_vars[key].startswith("your_"):
                warnings.append(f".env: {key} appears to be a placeholder")

        set_keys = [k for k, v in env_vars.items() if v and not v.startswith("your_")]
        print(f"  .env: {len(set_keys)} variable(s) set")

    # Report
    print()
    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"  ! {w}")
    if errors:
        print("\nErrors:")
        for e in errors:
            print(f"  x {e}")
        print("\nStatus: CONFIGURATION HAS ERRORS — run 'python wizard.py' to fix.")
        sys.exit(1)
    else:
        if warnings:
            print("\nStatus: Configuration OK with warnings.")
        else:
            print("Status: Configuration looks good!")
        sys.exit(0)


# ---------------------------------------------------------------------------
# Show next steps
# ---------------------------------------------------------------------------


def show_next_steps(project_root: str, repo_url: str) -> None:
    """Print the next-steps guidance after a successful setup.

    Args:
        project_root: Absolute path to the project root.
        repo_url: GitHub repo URL (may be empty if user skipped).
    """
    print()
    print("=" * 60)
    print("   Setup Complete! Next Steps")
    print("=" * 60)
    print()
    print("1. Review config.yml and .env in your project root.")
    print()
    print("2. Test locally:")
    print(f"     cd {project_root}")
    print("     python main.py")
    print()
    if repo_url:
        print("3. Your secrets are on GitHub. Check the Actions tab to")
        print("   trigger a manual run:")
        print(f"     {repo_url}/actions")
        print()
        print("4. The GitHub Actions workflow runs automatically on schedule.")
        print("   Edit .github/workflows/daily_brew.yml to change the cron.")
    else:
        print("3. To automate with GitHub Actions:")
        print("   a) Push this repo to GitHub.")
        print("   b) Add the secrets from .env to your repo's")
        print("      Settings -> Secrets and variables -> Actions.")
        print("   c) The workflow in .github/workflows/daily_brew.yml")
        print("      will run on your configured schedule.")
    print()
    print("   Tip: never commit config.yml or .env — they contain secrets.")
    print()


# ---------------------------------------------------------------------------
# Help text
# ---------------------------------------------------------------------------


def show_help() -> None:
    """Print usage information and exit."""
    print(__doc__)
    print("Commands:")
    print("  python wizard.py          Run the interactive setup wizard")
    print("  python wizard.py --help   Show this help message")
    print("  python wizard.py --check  Validate existing config.yml and .env")
    print()


# ---------------------------------------------------------------------------
# Project root resolution
# ---------------------------------------------------------------------------


def find_project_root() -> str:
    """Return the absolute path of the NewsBrewer project root.

    Walks upward from this file's directory, looking for ``requirements.txt``.

    Returns:
        Absolute path of the directory containing ``requirements.txt``.

    Raises:
        SystemExit: If the project root cannot be found.
    """
    current = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        if os.path.isfile(os.path.join(current, "requirements.txt")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    print("Error: Cannot find the project root (directory with requirements.txt).")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Main wizard orchestration
# ---------------------------------------------------------------------------


def run_wizard(project_root: str) -> None:
    """Run the full interactive setup wizard.

    Orchestrates all steps, generates output files, and optionally pushes
    secrets to GitHub.

    Args:
        project_root: Absolute path to the project root directory where
            ``config.yml`` and ``.env`` will be written.
    """
    print_banner()

    # Warn if files already exist
    config_path = os.path.join(project_root, "config.yml")
    env_path = os.path.join(project_root, ".env")
    if os.path.isfile(config_path) or os.path.isfile(env_path):
        print("Existing config.yml and/or .env detected.")
        overwrite = prompt_yes_no(
            "Overwrite with new configuration?", default=False
        )
        if not overwrite:
            print("Aborted. Run with --check to validate existing files.")
            sys.exit(0)
        print()

    # Step 1 — Provider selection
    providers = step_select_providers()

    # Step 2 — Per-account configuration
    print_section("Step 2: Email Account Credentials")
    accounts: list[dict[str, Any]] = []
    for i, provider in enumerate(providers, 1):
        acc = step_configure_account(provider, i)
        if acc:  # empty dict means user skipped after a failed connection
            accounts.append(acc)

    if not accounts:
        print("\nNo accounts configured successfully. Exiting.")
        sys.exit(1)

    # Step 3 — Newsletter senders
    senders = step_newsletter_senders()

    # Step 4 — AI focus
    ai_focus = step_ai_focus()

    # Step 5 — Delivery
    delivery = step_delivery(accounts)

    # Step 6 — GitHub
    repo_url = step_github()

    # --- Generate files ---
    print_section("Generating Configuration Files")

    config_data = build_config_yml(accounts, senders, ai_focus, delivery)
    env_lines = build_env_lines(accounts, delivery)

    write_config_yml(config_data, config_path)
    write_env_file(env_lines, env_path)

    # --- Optional GitHub secrets push ---
    if repo_url:
        push = prompt_yes_no(
            "\nPush secrets to GitHub Actions now?", default=True
        )
        if push:
            push_github_secrets(accounts, delivery, repo_url)

    show_next_steps(project_root, repo_url)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse command-line arguments and dispatch to the appropriate mode.

    Supports three modes:

    * No arguments  — run the interactive setup wizard.
    * ``--help``    — display usage information.
    * ``--check``   — validate existing config.yml and .env files.
    """
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        show_help()
        sys.exit(0)

    project_root = find_project_root()

    if "--check" in args:
        check_existing_config(project_root)
    else:
        try:
            run_wizard(project_root)
        except KeyboardInterrupt:
            print("\n\nSetup interrupted. No files were modified.")
            sys.exit(1)


if __name__ == "__main__":
    main()
