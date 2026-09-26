import asyncio
import json
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.ari_tools import Actor, AriTools
from ari.domain.tools.ari_permissions import ARI_TOOLS, CHAT
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.mcp_server.server import actor_from_env, build_server

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
ENV = {"ARI_ACTOR_ID": "42", "ARI_ACTOR_CHAT": "42", "ARI_ACTOR_NAME": "Gabriel",
       "ARI_ROLE": "owner", "ARI_CONTEXT": "chat", "ARI_TURN_ID": "t1"}


def test_actor_from_env():
    assert actor_from_env(ENV) == Actor("42", "42", "Gabriel", True, CHAT, "t1")
    assert actor_from_env({**ENV, "ARI_ROLE": "user"}).is_owner is False
    with pytest.raises(RuntimeError, match="ARI_TURN_ID"):
        actor_from_env({k: v for k, v in ENV.items() if k != "ARI_TURN_ID"})


async def test_server_registers_exactly_the_catalogue_and_calls_tools():
    conn = await connect(":memory:", embedding_dim=4)
    tools = AriTools(actor_from_env(ENV), schedule=SqliteScheduleStore(conn),
                     memory=SqliteMemoryAdapter(conn, embedding_dim=4),
                     turn_log=SqliteTurnLog(conn), tz=TZ, max_items=20, clock=lambda: NOW)

    async def get_tools():
        return tools

    server = build_server(get_tools)
    try:
        assert {t.name for t in await server.list_tools()} == set(ARI_TOOLS)
        result = await server.call_tool("recordar_dato", {"clave": "color", "valor": "azul"})
        assert result.content[0].text == "🧠 Guardé: color = azul"
    finally:
        await conn.close()


async def test_stdio_handshake_lists_tools_without_actor_env():
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "ari.mcp_server",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    async def send(obj):
        proc.stdin.write((json.dumps(obj) + "\n").encode())
        await proc.stdin.drain()

    async def read_id(wanted):
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=60)
            assert line, (await proc.stderr.read()).decode(errors="replace")[-2000:]
            msg = json.loads(line)
            if msg.get("id") == wanted:
                return msg

    try:
        await send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "test", "version": "0"}}})
        assert "result" in await read_id(1)
        await send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        await send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listed = await read_id(2)
        assert {t["name"] for t in listed["result"]["tools"]} == set(ARI_TOOLS)
    finally:
        proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
