from ari.domain.email.entities import EmailAccount
from ari.infrastructure.email.server_spec import (
    email_server_spec, server_name, valid_label,
)


def _acct():
    return EmailAccount("7", "trabajo", "imap.x.com", 993, True,
                        "smtp.x.com", 587, False, "juan@x.com", "pw")


def test_server_name_is_prefixed():
    assert server_name("trabajo") == "mail_trabajo"


def test_valid_label_rejects_unsafe():
    assert valid_label("trabajo")
    assert not valid_label("../x")
    assert not valid_label("Corp")   # uppercase
    assert not valid_label("")


def test_email_server_spec_builds_cli_env():
    spec = email_server_spec(_acct())
    assert spec["command"] == "npx"
    assert spec["args"] == ["-y", "mcp-mail-server@2.1.0"]
    assert spec["env"] == {
        "IMAP_HOST": "imap.x.com", "IMAP_PORT": "993", "IMAP_SECURE": "true",
        "SMTP_HOST": "smtp.x.com", "SMTP_PORT": "587", "SMTP_SECURE": "false",
        "EMAIL_USER": "juan@x.com", "EMAIL_PASS": "pw",
    }
