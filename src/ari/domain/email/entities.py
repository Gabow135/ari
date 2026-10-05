from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmailAccount:
    user_id: str
    label: str
    imap_host: str
    imap_port: int
    imap_secure: bool
    smtp_host: str
    smtp_port: int
    smtp_secure: bool
    email_user: str
    password: str


@dataclass(frozen=True, slots=True)
class EmailAccountSummary:
    label: str
    address: str


def mask_address(addr: str) -> str:
    local, _, domain = addr.partition("@")
    shown = local[:2] if len(local) > 2 else local[:1]
    masked = f"{shown}***"
    return f"{masked}@{domain}" if domain else masked


@dataclass(frozen=True, slots=True)
class MailboxSpec:
    cuenta: str
    imap_host: str
    imap_port: int
    imap_secure: bool
    user: str
    password: str


@dataclass(frozen=True, slots=True)
class EmailSummary:
    uid: str
    from_addr: str
    subject: str
    date: str
    snippet: str


@dataclass(frozen=True, slots=True)
class EmailAttachment:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class FetchedEmail:
    uid: str
    from_addr: str
    to_addr: str
    subject: str
    date: str
    body_text: str
    attachments: tuple[EmailAttachment, ...]
