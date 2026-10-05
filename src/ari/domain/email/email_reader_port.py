from typing import Protocol

from ari.domain.email.entities import EmailSummary, FetchedEmail, MailboxSpec


class EmailReaderPort(Protocol):
    """Reads mail over IMAP. Methods are synchronous/blocking; callers run them
    off the event loop (e.g. asyncio.to_thread)."""

    def search(self, spec: MailboxSpec, criteria: str, limit: int) -> list[EmailSummary]: ...

    def fetch(self, spec: MailboxSpec, uid: str) -> FetchedEmail: ...
