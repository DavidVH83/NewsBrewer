# Contributing to NewsBrewer

Thank you for your interest in contributing to NewsBrewer! This document explains how to get started, how the codebase is organised, and what we expect from pull requests.

---

## Table of Contents

1. [Development Environment](#development-environment)
2. [Running the Tests](#running-the-tests)
3. [Code Style](#code-style)
4. [Adding a New Email Provider](#adding-a-new-email-provider)
5. [Adding a New AI Model Provider](#adding-a-new-ai-model-provider)
6. [Pull Request Checklist](#pull-request-checklist)
7. [Code of Conduct](#code-of-conduct)

---

## Development Environment

### Requirements

- Python 3.11 or newer
- Git

### Setup

1. **Fork and clone** the repository:

   ```bash
   git clone https://github.com/YOUR_USERNAME/newsbrewer.git
   cd newsbrewer
   ```

2. **Create a virtual environment** (strongly recommended):

   ```bash
   python -m venv .venv
   source .venv/bin/activate      # macOS / Linux
   .venv\Scripts\activate         # Windows
   ```

3. **Install dependencies** (includes test dependencies):

   ```bash
   pip install -r requirements.txt
   ```

4. **Copy the example config**:

   ```bash
   cp config.example.yml config.yml
   ```

5. **Create a `.env` file** for credentials (never commit this):

   ```bash
   # .env
   ACCOUNT_1_EMAIL=you@example.com
   ACCOUNT_1_PASSWORD=your-app-password
   SMTP_EMAIL=you@example.com
   SMTP_PASSWORD=your-app-password
   DIGEST_RECIPIENT=you@example.com
   GITHUB_TOKEN=your-github-personal-access-token
   ```

6. **Test your setup** without sending any email:

   ```bash
   python main.py --dry-run
   ```

---

## Running the Tests

The test suite uses [pytest](https://docs.pytest.org/). Run all tests with:

```bash
python -m pytest tests/ -v
```

Run a single test file:

```bash
python -m pytest tests/test_analyst_agent.py -v
```

Run tests matching a keyword:

```bash
python -m pytest tests/ -v -k "email"
```

Tests are located in `tests/` and use fixtures from `tests/fixtures/`. The test suite does not make real network calls — all HTTP and IMAP interactions are mocked.

When adding new features, add or update the corresponding test file. Pull requests that reduce test coverage will not be merged.

---

## Code Style

NewsBrewer follows these conventions throughout the codebase. Please match them in your contributions.

### Type Hints

All function signatures must include type hints for parameters and return values:

```python
# Good
def fetch_article(url: str, timeout: int = 10) -> Article | None:
    ...

# Not acceptable
def fetch_article(url, timeout=10):
    ...
```

### Docstrings

Every module, class, and public function must have a docstring in Google style:

```python
def score_article(content: str, topics: list[str]) -> float:
    """Score an article for relevance to a list of topics.

    Args:
        content: The plain-text article body.
        topics: List of topic strings from the user's config.

    Returns:
        A relevance score between 0.0 and 10.0.

    Raises:
        ValueError: If content is empty.
    """
```

### No Hardcoded Credentials

Credentials, tokens, and passwords must **never** appear in source code or config files that are committed to the repository. Use environment variables (via `.env` locally, GitHub Secrets in CI). The `config_loader` reads credentials from environment variables automatically.

### General Guidelines

- Keep functions short and focused on a single responsibility.
- Prefer explicit over implicit — avoid clever one-liners that obscure intent.
- Use the existing `get_logger(__name__)` pattern for logging; do not use `print()` in library code.
- Do not catch bare `Exception` unless you are at the top of the call stack (as in `orchestrator.py`).
- Format your code with a standard Python formatter (e.g. `black`) before submitting.

---

## Adding a New Email Provider

NewsBrewer connects to email via IMAP, so any provider that supports IMAP over SSL (port 993) will work without code changes. To **document** a new provider or add any provider-specific workarounds:

1. **Verify the IMAP settings** for the provider (server hostname, port, authentication method).

2. **Update `config.example.yml`** to include commented-out example entries for the new provider:

   ```yaml
   email_sources:
     accounts:
       - name: "My Provider"
         imap_server: "imap.example.com"
         imap_port: 993
         email: ""          # Set as ACCOUNT_N_EMAIL in .env
         app_password: ""   # Set as ACCOUNT_N_PASSWORD in .env
   ```

3. **Update `README.md`** with setup instructions under the "Email Account Setup" section (how to generate an app password, any 2FA requirements, etc.).

4. If the provider requires non-standard authentication (e.g. OAuth2 instead of app passwords), add a new authentication method to `src/utils/imap_helper.py`. The existing helpers are:
   - `connect(server, port, email, password)` — SSL login with username/password
   - `disconnect(conn)` — clean logout
   - `search_since(conn, date)` — search for emails since a given date

   Add your method alongside these with full type hints and a docstring, and update the `connect()` function or the `EmailAgent` to select the correct method based on config.

5. Add a test for any new imap_helper functionality in `tests/test_email_agent.py`.

---

## Adding a New AI Model Provider

The AI analysis step is handled by `src/agents/analyst_agent.py`, which calls a provider from `src/providers/`.

### Step 1 — Create the provider module

Create `src/providers/your_provider.py`. Model your implementation on the existing `src/providers/github_models.py`. Your provider must expose a function (or class) that accepts a prompt string and returns the model's text response:

```python
# src/providers/your_provider.py

"""Client for the YourProvider AI API."""

from src.utils.config_loader import ModelConfig
from src.utils.logger import get_logger

logger = get_logger(__name__)


def call_model(prompt: str, config: ModelConfig) -> str:
    """Send a prompt to YourProvider and return the text response.

    Args:
        prompt: The full prompt string to send to the model.
        config: Model configuration from config.yml.

    Returns:
        The model's text response as a plain string.

    Raises:
        RuntimeError: If the API call fails after retries.
    """
    ...
```

### Step 2 — Register the provider

In `src/providers/__init__.py` (or wherever provider dispatch lives), add your new provider to the routing logic so it can be selected via `config.yml`:

```yaml
model:
  provider: "your_provider"
  name: "your-model-name"
```

### Step 3 — Handle authentication

Read any API keys from environment variables using `os.environ.get()` or the existing `config_loader` pattern. Never accept credentials as function arguments that could appear in logs.

### Step 4 — Add tests

Add a test file `tests/test_your_provider.py` that mocks the HTTP layer (use `respx` as the other tests do) and verifies correct request construction and response parsing.

### Step 5 — Document it

Update `README.md` and `config.example.yml` to explain how to configure the new provider.

---

## Pull Request Checklist

Before submitting a pull request, please verify all of the following:

- [ ] All existing tests pass: `python -m pytest tests/ -v`
- [ ] New or changed behaviour is covered by tests
- [ ] All new functions and classes have type hints and docstrings
- [ ] No credentials, passwords, or tokens appear anywhere in the diff
- [ ] `config.yml` and `.env` are not included in the commit
- [ ] The PR description explains *what* changed and *why*
- [ ] For bug fixes: the PR references the issue it resolves
- [ ] For new features: the PR includes updates to `README.md` if user-facing behaviour changed

---

## Code of Conduct

NewsBrewer is an open and welcoming project. Contributors are expected to:

- Be respectful and constructive in all interactions — in issues, pull requests, and discussions.
- Assume good intent from others.
- Accept feedback gracefully; give feedback kindly.
- Focus criticism on code and ideas, not on people.

Harassment, discrimination, or personal attacks of any kind will not be tolerated and may result in being removed from the project. If you experience or witness unacceptable behaviour, please open a private issue or contact the maintainers directly.

We are all here because we find this problem interesting. Let's keep it that way.
