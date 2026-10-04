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
