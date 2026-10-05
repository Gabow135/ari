import json

import pytest

from ari.application.tools.tool_policy import AriServerSpec, ToolPolicy, no_turn
from ari.domain.tools.ari_permissions import CHAT, TASK
from ari.infrastructure.tools.turn_config import TurnConfigWriter


class FakeRegistry:
    def __init__(self, owner=None):
        self._owner = owner or {}

    def resolved(self, is_owner):
        return dict(self._owner) if is_owner else {}

    def servers_for(self, is_owner):
        names = tuple(self.resolved(is_owner))
        return names, None

    def descriptions(self, is_owner):
        return [(n, "desc") for n in self.resolved(is_owner)]

    def degraded_for(self, is_owner):
        return []


SPEC = AriServerSpec("py", ("-m", "ari.mcp_server"), {"ARI_DB_PATH": "/db"})


def _policy(tmp_path, owner_servers=None):
    return ToolPolicy(FakeRegistry(owner_servers), is_owner=lambda uid: uid == "42",
                      ari=SPEC, writer=TurnConfigWriter(str(tmp_path)))


async def test_owner_chat_turn(tmp_path):
    policy = _policy(tmp_path, {"google": {"command": "uvx"}})
    async with policy.turn("42", CHAT, "Gabriel", "42") as turn:
        t = turn.toolset
        assert "ToolSearch" in t.builtin_tools
        assert "mcp__google" in t.allowed_tools
        assert "mcp__ari__enviar_mensaje" in t.allowed_tools
        with open(t.mcp_config_path, encoding="utf-8") as f:
            servers = json.load(f)["mcpServers"]
        env = servers["ari"]["env"]
        assert servers["ari"]["command"] == "py" and servers["ari"]["args"] == ["-m", "ari.mcp_server"]
        assert env == {"ARI_DB_PATH": "/db", "ARI_ACTOR_ID": "42", "ARI_ACTOR_CHAT": "42",
                       "ARI_ACTOR_NAME": "Gabriel", "ARI_ROLE": "owner", "ARI_CONTEXT": "chat",
                       "ARI_TURN_ID": turn.turn_id, "ARI_EMAIL_ACCOUNTS": "[]"}
        path = t.mcp_config_path
    import os
    assert not os.path.exists(path)


async def test_user_task_turn_is_read_only_and_has_no_owner_servers(tmp_path):
    policy = _policy(tmp_path, {"google": {"command": "uvx"}})
    async with policy.turn("7", TASK) as turn:
        allowed = turn.toolset.allowed_tools
        assert "mcp__google" not in allowed
        assert [a for a in allowed if a.startswith("mcp__ari__")] == [
            "mcp__ari__listar_agenda", "mcp__ari__ver_datos"]


async def test_config_deleted_even_when_the_body_raises(tmp_path):
    policy = _policy(tmp_path)
    with pytest.raises(RuntimeError):
        async with policy.turn("42") as turn:
            path = turn.toolset.mcp_config_path
            raise RuntimeError("llm exploded")
    import os
    assert not os.path.exists(path)


async def test_without_ari_spec_falls_back_to_3a_behaviour(tmp_path):
    policy = ToolPolicy(FakeRegistry(), is_owner=lambda uid: False)
    async with policy.turn("7") as turn:
        assert turn.toolset.allowed_tools == ("WebSearch", "WebFetch")
        assert turn.turn_id


async def test_writer_failure_falls_back_to_web_only(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    policy = ToolPolicy(FakeRegistry(), is_owner=lambda uid: True, ari=SPEC,
                        writer=TurnConfigWriter(str(blocker)))
    async with policy.turn("42") as turn:
        assert turn.toolset.allowed_tools == ("WebSearch", "WebFetch")


async def test_no_turn_yields_none():
    async with no_turn() as turn:
        assert turn is None


async def test_turn_injects_email_accounts(tmp_path):
    from ari.infrastructure.email.server_spec import mailboxes_from_json
    mail = {"email_corp": {"command": "npx", "args": ["-y", "mcp-mail-server@2.1.0"],
                           "env": {"IMAP_HOST": "imap.corp.com", "IMAP_PORT": "993",
                                   "IMAP_SECURE": "true", "EMAIL_USER": "me@corp.com",
                                   "EMAIL_PASS": "s3cret"}}}
    policy = _policy(tmp_path, mail)
    async with policy.turn("42", CHAT, "Gabriel", "42") as turn:
        with open(turn.toolset.mcp_config_path, encoding="utf-8") as f:
            env = json.load(f)["mcpServers"]["ari"]["env"]
    boxes = mailboxes_from_json(env["ARI_EMAIL_ACCOUNTS"])
    assert set(boxes) == {"email_corp"}
    assert boxes["email_corp"].imap_host == "imap.corp.com"
    assert boxes["email_corp"].password == "s3cret"


async def test_turn_without_mailboxes_has_empty_accounts(tmp_path):
    policy = _policy(tmp_path, {"google": {"command": "uvx", "args": ["workspace-mcp"]}})
    async with policy.turn("42", CHAT, "Gabriel", "42") as turn:
        with open(turn.toolset.mcp_config_path, encoding="utf-8") as f:
            env = json.load(f)["mcpServers"]["ari"]["env"]
    assert env["ARI_EMAIL_ACCOUNTS"] == "[]"
