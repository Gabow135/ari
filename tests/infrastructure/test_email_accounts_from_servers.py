from ari.infrastructure.email.server_spec import (
    email_accounts_from_servers,
    mailboxes_from_json,
    mailboxes_to_json,
)

SERVERS = {
    "google": {"command": "uvx", "args": ["workspace-mcp"], "env": {}},
    "email_corp": {"command": "npx", "args": ["-y", "mcp-mail-server@2.1.0"],
                   "env": {"IMAP_HOST": "imap.corp.com", "IMAP_PORT": "993",
                           "IMAP_SECURE": "true", "EMAIL_USER": "me@corp.com",
                           "EMAIL_PASS": "s3cret"}},
    "mail_trabajo": {"command": "npx", "args": ["-y", "mcp-mail-server@2.1.0"],
                     "env": {"IMAP_HOST": "imap.gmail.com", "IMAP_PORT": "993",
                             "IMAP_SECURE": "true", "EMAIL_USER": "u@gmail.com",
                             "EMAIL_PASS": "app-pass"}},
    "ari": {"command": "py", "args": ["-m", "ari.mcp_server"], "env": {}},
}


def test_extracts_only_mail_servers():
    specs = {s.cuenta: s for s in email_accounts_from_servers(SERVERS)}
    assert set(specs) == {"email_corp", "mail_trabajo"}
    assert specs["email_corp"].imap_host == "imap.corp.com"
    assert specs["email_corp"].imap_port == 993
    assert specs["email_corp"].imap_secure is True
    assert specs["mail_trabajo"].password == "app-pass"


def test_skips_mail_servers_missing_host_or_user():
    broken = {"mail_x": {"args": ["mcp-mail-server@9"], "env": {"IMAP_HOST": "h"}}}
    assert email_accounts_from_servers(broken) == []


def test_json_roundtrip():
    specs = email_accounts_from_servers(SERVERS)
    restored = mailboxes_from_json(mailboxes_to_json(specs))
    assert set(restored) == {"email_corp", "mail_trabajo"}
    assert restored["mail_trabajo"].user == "u@gmail.com"


def test_from_json_tolerates_garbage():
    assert mailboxes_from_json("not json") == {}
    assert mailboxes_from_json("") == {}
