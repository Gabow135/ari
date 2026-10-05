import json
import re

from ari.domain.email.entities import EmailAccount, MailboxSpec

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


_MAIL_MARKER = "mcp-mail-server"


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes")


def email_accounts_from_servers(servers: dict) -> list[MailboxSpec]:
    """Pick the mail servers out of a per-turn MCP config and turn each into a
    MailboxSpec keyed by its config name (the turn-visible account name)."""
    specs: list[MailboxSpec] = []
    for name, cfg in (servers or {}).items():
        args = cfg.get("args") or []
        if not any(_MAIL_MARKER in str(a) for a in args):
            continue
        env = cfg.get("env") or {}
        host, user = env.get("IMAP_HOST"), env.get("EMAIL_USER")
        if not host or not user:
            continue
        try:
            port = int(env.get("IMAP_PORT", "993"))
        except (TypeError, ValueError):
            port = 993
        specs.append(MailboxSpec(
            cuenta=name, imap_host=host, imap_port=port,
            imap_secure=_truthy(env.get("IMAP_SECURE", "true")),
            user=user, password=env.get("EMAIL_PASS", "")))
    return specs


def mailboxes_to_json(specs: list[MailboxSpec]) -> str:
    return json.dumps([
        {"cuenta": s.cuenta, "imap_host": s.imap_host, "imap_port": s.imap_port,
         "imap_secure": s.imap_secure, "user": s.user, "password": s.password}
        for s in specs])


def mailboxes_from_json(text: str) -> dict[str, MailboxSpec]:
    try:
        raw = json.loads(text or "[]")
    except (TypeError, ValueError):
        return {}
    out: dict[str, MailboxSpec] = {}
    for d in raw if isinstance(raw, list) else []:
        try:
            out[d["cuenta"]] = MailboxSpec(
                cuenta=d["cuenta"], imap_host=d["imap_host"],
                imap_port=int(d["imap_port"]), imap_secure=bool(d["imap_secure"]),
                user=d["user"], password=d.get("password", ""))
        except (KeyError, TypeError, ValueError):
            continue
    return out
