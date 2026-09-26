import json
import os
import shutil
from datetime import datetime, timezone

import pytest

from ari.application.tools.tool_policy import ToolPolicy
from ari.config.settings import Settings
from ari.domain.agent.message import Message
from ari.domain.ports.progress_port import TOOL, current_progress
from ari.infrastructure.claude_env import claude_cli_env
from ari.infrastructure.llm.claude_code_adapter import ClaudeCodeCliAdapter
from ari.infrastructure.tools.mcp_registry import McpRegistry


class _Sink:
    def __init__(self):
        self.tools = []

    def emit(self, event):
        if event.kind == TOOL:
            self.tools.append(event.name)


def _llm():
    s = Settings()
    return ClaudeCodeCliAdapter(cli_env=claude_cli_env(s.claude_oauth_token,
                                                       s.claude_config_dir), timeout=240)


async def _ask(policy, user_id, question):
    sink = _Sink()
    token = current_progress.set(sink)
    try:
        reply = await _llm().complete("Eres un asistente. Responde breve.",
                                      [Message(user_id, "user", question,
                                               datetime.now(timezone.utc))],
                                      toolset=policy.for_user(user_id))
    finally:
        current_progress.reset(token)
    return reply, sink.tools


@pytest.mark.slow
async def test_web_search_for_approved_user(tmp_path):
    registry = McpRegistry(str(tmp_path / "none.json"), ".env", str(tmp_path / "out"))
    policy = ToolPolicy(registry, is_owner=lambda uid: False)
    reply, tools = await _ask(policy, "7", "Busca en la web la capital de Mongolia y "
                                           "responde solo el nombre.")
    assert "WebSearch" in tools or "WebFetch" in tools
    assert "ulán" in reply.lower() or "ulaanbaatar" in reply.lower()


@pytest.mark.slow
async def test_mcp_server_via_config_on_this_platform(tmp_path):
    if shutil.which("npx") is None:
        pytest.skip("npx not installed")
    cfg = tmp_path / "servers.json"
    cfg.write_text(json.dumps({"mcpServers": {"demo": {
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-everything"],
        "description": "demo"}}}), encoding="utf-8")
    registry = McpRegistry(str(cfg), ".env", str(tmp_path / "out"))
    policy = ToolPolicy(registry, is_owner=lambda uid: uid == "42")
    reply, tools = await _ask(policy, "42", "Usa la herramienta de suma del servidor demo "
                                            "para sumar 21 y 21 y responde solo el número.")
    assert any(t.startswith("mcp__demo__") for t in tools)
    assert "42" in reply


@pytest.mark.slow
async def test_mysql_show_tables():
    s = Settings()
    registry = McpRegistry(s.mcp_config, ".env", os.path.join(s.claude_config_dir, "mcp"))
    if "mysql" not in registry.servers_for(True)[0]:
        pytest.skip("mysql server disabled: " + str([x.detail for x in registry.status()]))
    policy = ToolPolicy(registry, is_owner=lambda uid: True)
    reply, tools = await _ask(policy, "42", "Lista las tablas de la base de datos (SHOW TABLES).")
    assert any(t.startswith("mcp__mysql__") for t in tools)


@pytest.mark.slow
async def test_google_last_emails():
    s = Settings()
    registry = McpRegistry(s.mcp_config, ".env", os.path.join(s.claude_config_dir, "mcp"))
    if "google" not in registry.servers_for(True)[0]:
        pytest.skip("google server disabled: " + str([x.detail for x in registry.status()]))
    policy = ToolPolicy(registry, is_owner=lambda uid: True)
    reply, tools = await _ask(policy, "42", "¿Cuáles son los asuntos de mis últimos 3 correos?")
    assert any(t.startswith("mcp__google__") for t in tools)
