import re

from ari.domain.email.entities import EmailAccount

LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")
MAIL_PACKAGE = "mcp-mail-server@2.1.0"


def valid_label(label: str) -> bool:
    return bool(LABEL_RE.match(label or ""))


def server_name(label: str) -> str:
    return f"mail_{label}"


def _b(flag: bool) -> str:
    return "true" if flag else "false"


def email_server_spec(account: EmailAccount) -> dict:
    return {
        "command": "npx",
        "args": ["-y", MAIL_PACKAGE],
        "env": {
            "IMAP_HOST": account.imap_host, "IMAP_PORT": str(account.imap_port),
            "IMAP_SECURE": _b(account.imap_secure),
            "SMTP_HOST": account.smtp_host, "SMTP_PORT": str(account.smtp_port),
            "SMTP_SECURE": _b(account.smtp_secure),
            "EMAIL_USER": account.email_user, "EMAIL_PASS": account.password,
        },
    }
