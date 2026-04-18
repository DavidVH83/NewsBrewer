# Changelog

All notable changes to NewsBrewer are documented here.

## [Unreleased]

### Added
- Initial public release
- Gmail and Outlook support via IMAP
- AI-powered article filtering via GitHub Models (gpt-4o-mini)
- Beautiful HTML digest email with narrative generation
- SQLite knowledge base with FTS5 full-text search
- Article rating system via GitHub Issues
- Manual link forwarding via BREW: email subject
- Flask-based configuration UI (`config_ui.py`)
- Interactive setup wizard (`wizard.py`)
- GitHub Actions workflow for daily automated runs
- Per-domain article capping (max 2 per domain)
- Feedback-based relevance adjustment
- Medium RSS feed fallback for paywalled articles
- Rate limiting and retry logic for GitHub Models API
- Full pytest test suite with async support

---

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
