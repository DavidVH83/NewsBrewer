"""Data models for the NewsBrewer pipeline.

Exports:
    EmailMessage — raw email fetched from IMAP.
    Article      — web article fetched from a URL.
    DigestItem   — ranked and summarised item ready for the digest.
"""

from src.models.email_message import EmailMessage
from src.models.article import Article
from src.models.digest_item import DigestItem

__all__ = ["EmailMessage", "Article", "DigestItem"]
