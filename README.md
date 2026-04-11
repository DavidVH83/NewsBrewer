# 🍺 NewsBrewer

> Brew your daily AI knowledge digest from any inbox

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![MIT License](https://img.shields.io/badge/license-MIT-green)
![GitHub Actions](https://img.shields.io/badge/runs%20on-GitHub%20Actions-2088FF)

---

## What is NewsBrewer?

You subscribe to newsletters because you want to stay informed — but you never have time to read them all. They pile up, you feel guilty, and the signal you actually care about gets buried in the noise.

**NewsBrewer** solves this by automatically reading your newsletter inbox every morning, extracting every article link, and using AI to score each one for relevance against topics you define. Only the articles that match your interests make it into a clean, beautiful HTML digest email that arrives in your inbox before you start your day.

The whole pipeline runs on **GitHub Actions for free** — no servers, no cloud bills, no subscriptions. Your credentials stay in GitHub Secrets and your reading history lives in a local SQLite database that travels with your repository. NewsBrewer is entirely self-hosted and open source, so you control everything.

---

## Features

- ✅ Connects to any email provider via IMAP (Gmail, Outlook, Yahoo, iCloud, custom domains)
- ✅ Reads newsletter emails from the last 24 hours automatically
- ✅ Supports manual link forwarding — send yourself an email with subject `BREW:` to add any URL
- ✅ AI-powered relevance filtering via GitHub Models (gpt-4o-mini, completely free)
- ✅ Beautiful HTML digest email with relevance scores for every article
- ✅ Local SQLite knowledge base with full-text search across everything you've ever read
- ✅ Runs automatically via GitHub Actions at your chosen time (zero cost, zero maintenance)
- ✅ Course and learning resource detection with special highlighting in the digest

---

## How It Works

```
Email Inbox(es)
      │
      ▼
 Email Agent  ──────────────────────────────────────────────┐
(reads IMAP, last 24h)                               Manual Link Agent
      │                                              (BREW: subject emails)
      ▼                                                      │
 URL Extractor  ◄───────────────────────────────────────────┘
(deduplicates, skips already-seen URLs)
      │
      ▼
 Fetcher Agent
(async HTTP fetch + content extraction)
      │
      ▼
 Analyst Agent  ──► Knowledge Base (SQLite)
(AI scoring via GitHub Models)
      │
      ▼
 Digest Agent
(renders HTML email, sends via SMTP)
      │
      ▼
Your Inbox 🍺
```

Each URL found in your newsletters is fetched, its content extracted, and then scored by GPT-4o-mini against the topics you care about (configured in `config.yml`). Articles scoring above your threshold are assembled into a ranked digest and delivered to you.

---

## Quick Start

### Prerequisites

- Python 3.11+
- A Gmail or Outlook account that receives newsletters
- A GitHub account (for free AI via GitHub Models and free scheduled runs via GitHub Actions)

### 1. Fork and Clone

Fork this repository on GitHub, then clone your fork:

```bash
git clone https://github.com/YOUR_USERNAME/newsbrewer.git
cd newsbrewer
pip install -r requirements.txt
```

### 2. Configure

Copy the example config and edit it to match your setup:

```bash
cp config.example.yml config.yml
```

Open `config.yml` and fill in:
- Your newsletter sender addresses under `email_sources.newsletter_senders`
- The topics you care about under `ai_focus.interesting_topics`
- Your SMTP delivery settings under `delivery`

Credentials (passwords, tokens) are **never stored in `config.yml`**. Instead, create a `.env` file in the project root:

```bash
# .env — never commit this file
ACCOUNT_1_EMAIL=you@gmail.com
ACCOUNT_1_PASSWORD=your-app-password
SMTP_EMAIL=you@gmail.com
SMTP_PASSWORD=your-app-password
DIGEST_RECIPIENT=you@gmail.com
GITHUB_TOKEN=your-github-pat
```

> **Important:** `.env` and `config.yml` are listed in `.gitignore`. Never commit them.

### 3. Test Locally

Run the pipeline without sending any email:

```bash
python main.py --dry-run
```

This executes every stage (email collection, fetching, AI analysis) and logs what the digest *would* contain, without actually sending anything. It's the safest way to verify your configuration.

### 4. Configure GitHub Secrets

In your forked repository, go to **Settings → Secrets and variables → Actions** and add these secrets:

| Secret name | Description |
|---|---|
| `ACCOUNT_1_EMAIL` | Email address for your first newsletter inbox |
| `ACCOUNT_1_PASSWORD` | App password for that account |
| `ACCOUNT_2_EMAIL` | *(Optional)* Second inbox email address |
| `ACCOUNT_2_PASSWORD` | *(Optional)* App password for second inbox |
| `SMTP_EMAIL` | Email address used to send the digest |
| `SMTP_PASSWORD` | App password for the sending account |
| `DIGEST_RECIPIENT` | Email address where you want to receive the digest |

`GITHUB_TOKEN` is provided automatically by GitHub Actions — you do not need to add it.

### 5. Enable GitHub Actions

The workflow file at `.github/workflows/daily_brew.yml` is already configured to run at **06:00 UTC daily**. Once your secrets are added, GitHub Actions will start running automatically.

You can also trigger it manually at any time from the **Actions** tab in your repository by clicking **Run workflow** on the "NewsBrewer Daily Digest" workflow.

---

## Configuration

### config.yml

The main configuration file has four sections:

**`email_sources`** — which IMAP accounts to read from, which sender addresses count as newsletters, and the keyword used for manual link forwarding (`BREW` by default).

**`ai_focus`** — the heart of your personalisation. List the topics you find interesting, the topics you want filtered out, the minimum relevance score an article must reach to appear in your digest (default: `6.0` out of 10), and whether to highlight courses.

**`delivery`** — SMTP settings for sending the digest, who to send it to, maximum articles per digest, and the cron schedule.

**`model`** — which AI provider and model to use. The default is `github_models` / `gpt-4o-mini`, which is free with a GitHub account.

See `config.example.yml` for the full annotated reference.

### Email Account Setup

#### Gmail

1. Enable 2-Factor Authentication on your Google account
2. Go to [https://myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Create a new app password — name it anything (e.g. "NewsBrewer")
4. Use the generated 16-character password as `ACCOUNT_1_PASSWORD` (not your normal Google password)
5. IMAP server: `imap.gmail.com`, port: `993`

#### Outlook / Microsoft 365

1. Enable 2-Factor Authentication on your Microsoft account
2. Go to [https://account.microsoft.com/security](https://account.microsoft.com/security) → Advanced security options → App passwords
3. Create an app password for NewsBrewer
4. Use it as `ACCOUNT_1_PASSWORD`
5. IMAP server: `outlook.office365.com`, port: `993`

#### Yahoo Mail

1. Go to **Account Security** in your Yahoo account settings
2. Enable "Allow apps that use less secure sign in" or generate an app password
3. IMAP server: `imap.mail.yahoo.com`, port: `993`

#### iCloud Mail

1. Go to [https://appleid.apple.com](https://appleid.apple.com) → Sign-In and Security → App-Specific Passwords
2. Generate a password for NewsBrewer
3. IMAP server: `imap.mail.me.com`, port: `993`

#### Custom Domain

Use whatever IMAP server your host provides. Most support port `993` with SSL.

---

## Manual Link Forwarding

Found an interesting article outside your newsletters? Send yourself an email with the subject line beginning with `BREW:` and paste the URL anywhere in the body. NewsBrewer will detect it and include it in the next digest run.

**Example:**

- Subject: `BREW: Interesting paper on multi-agent coordination`
- Body: paste the URL (e.g. `https://arxiv.org/abs/2404.12345`)

The keyword (`BREW`) can be changed in `config.yml` under `email_sources.manual_keyword`.

---

## Searching Your Knowledge Base

Every article that makes it into a digest is stored in a local SQLite database at `data/knowledge_base.db`. You can search it with the built-in CLI:

```bash
# Full-text search by topic or keyword
python search.py "RAG retrieval"

# Show all saved courses and learning resources
python search.py --courses

# Courses added in the last 30 days
python search.py --courses --days 30

# Database statistics (total articles, date range, top topics)
python search.py --stats

# Limit the number of results shown
python search.py "LangGraph" --limit 5

# Use a different database file
python search.py "transformers" --db /path/to/other.db
```

Full-text search uses SQLite FTS5, so you can use FTS5 query syntax (e.g. `"exact phrase"`, `term1 OR term2`, `term*` for prefix matching).

---

## Project Structure

```
newsbrewer/
├── main.py                    # Entry point — run the daily brew
├── search.py                  # CLI search tool for the knowledge base
├── config.example.yml         # Annotated configuration reference
├── requirements.txt           # Python dependencies
├── pytest.ini                 # Test configuration
│
├── src/
│   ├── agents/
│   │   ├── orchestrator.py    # Wires all agents together, runs the pipeline
│   │   ├── email_agent.py     # Reads newsletter emails via IMAP
│   │   ├── manual_link_agent.py  # Reads BREW: emails for manual links
│   │   ├── fetcher_agent.py   # Async HTTP fetcher and content extractor
│   │   ├── analyst_agent.py   # AI scoring and filtering via GitHub Models
│   │   └── digest_agent.py    # Renders and sends the HTML email digest
│   │
│   ├── knowledge/
│   │   ├── database.py        # SQLite knowledge base with FTS5 search
│   │   └── search.py          # Result formatting for the search CLI
│   │
│   ├── models/
│   │   ├── article.py         # Data class for fetched article content
│   │   ├── digest_item.py     # Data class for an AI-scored digest entry
│   │   └── email_message.py   # Data class for a parsed email message
│   │
│   ├── providers/
│   │   └── github_models.py   # GitHub Models API client (OpenAI-compatible)
│   │
│   └── utils/
│       ├── config_loader.py   # Loads and validates config.yml + .env
│       ├── html_builder.py    # Jinja2 HTML email renderer
│       ├── imap_helper.py     # IMAP connection helpers
│       ├── logger.py          # Structured logging setup
│       └── url_extractor.py   # URL extraction from email bodies
│
├── templates/
│   └── digest_email.html      # Jinja2 template for the digest email
│
├── tests/                     # Pytest test suite
│   ├── fixtures/              # Shared test fixtures
│   ├── test_analyst_agent.py
│   ├── test_digest_agent.py
│   ├── test_email_agent.py
│   ├── test_fetcher_agent.py
│   └── test_knowledge_database.py
│
├── data/
│   └── knowledge_base.db      # SQLite database (auto-created, git-ignored)
│
├── logs/                      # Log files (git-ignored)
│
└── .github/
    └── workflows/
        └── daily_brew.yml     # GitHub Actions workflow — runs daily at 06:00 UTC
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set up a development environment, run the test suite, add new email or AI providers, and submit pull requests.

---

## License

MIT — see [LICENSE](LICENSE).
