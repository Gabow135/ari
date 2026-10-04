import json
import pytest

from ari.application.tools.tool_policy import AriServerSpec, ToolPolicy
from ari.domain.email.entities import EmailAccount


class FakeRegistry:
    def servers_for(self, is_owner): return ((), None)
    def resolved(self, is_owner): return {}
    def descriptions(self, is_owner): return []
    def degraded_for(self, is_owner): return []
    def status(self): return []


class FakeWriter:
    def __init__(self): self.written = None
    def write(self, servers): self.written = servers; return "/tmp/turn.json"
    def remove(self, path): pass


class FakeAccounts:
    def __init__(self, by_user): self._by = by_user
    async def list_for_user(self, user_id): return self._by.get(user_id, [])
    async def summaries_for(self, user_id): return []


def _acct(user_id, label):
    return EmailAccount(user_id, label, "imap.x.com", 993, True, "smtp.x.com", 465, True,
                        f"{label}@x.com", "pw")


@pytest.fixture
def writer():
    return FakeWriter()


async def test_turn_injects_only_acting_users_mailboxes(writer):
    accounts = FakeAccounts({"7": [_acct("7", "trabajo")], "9": [_acct("9", "otra")]})
    policy = ToolPolicy(FakeRegistry(), lambda uid: False,
                        ari=AriServerSpec("py", ("-m", "x")), writer=writer,
                        email_accounts=accounts)
    async with policy.turn("7") as turn:
        assert "mail_trabajo" in writer.written
        assert "mail_otra" not in writer.written
        assert writer.written["mail_trabajo"]["env"]["EMAIL_USER"] == "trabajo@x.com"
        assert "mcp__mail_trabajo" in turn.toolset.allowed_tools


async def test_turn_without_email_accounts_is_unchanged(writer):
    policy = ToolPolicy(FakeRegistry(), lambda uid: False,
                        ari=AriServerSpec("py", ("-m", "x")), writer=writer)
    async with policy.turn("7"):
        assert not any(k.startswith("mail_") for k in writer.written)
