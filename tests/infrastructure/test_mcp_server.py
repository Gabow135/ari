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


async def test_proponer_codigo_registered_only_for_owner_chat():
    async def get_tools():
        raise AssertionError("not called")

    from ari.domain.tools.ari_permissions import allowed_ari_tools
    owner_server = build_server(get_tools, allowed_ari_tools(True, "chat"))
    user_server = build_server(get_tools, allowed_ari_tools(False, "chat"))
    owner = {t.name for t in await owner_server.list_tools()}
    user = {t.name for t in await user_server.list_tools()}
    assert "proponer_codigo" in owner and "proponer_codigo" not in user


async def test_proponer_codigo_description_does_not_require_slash_code():
    async def get_tools():
        raise AssertionError("not called")

    from ari.domain.tools.ari_permissions import allowed_ari_tools
    server = build_server(get_tools, allowed_ari_tools(True, "chat"))
    tool = next(t for t in await server.list_tools() if t.name == "proponer_codigo")
    desc = tool.description
    # The tool IS the natural-language path, so its own description must not send
    # the owner to the /code command; it still gates execution behind «dale».
    assert "/code" not in desc
    assert "dale" in desc.lower()


async def test_build_server_with_allowed_list_exposes_only_those_tools():
    conn = await connect(":memory:", embedding_dim=4)
    tools = AriTools(actor_from_env(ENV), schedule=SqliteScheduleStore(conn),
                     memory=SqliteMemoryAdapter(conn, embedding_dim=4),
                     turn_log=SqliteTurnLog(conn), tz=TZ, max_items=20, clock=lambda: NOW)

    async def get_tools():
        return tools

    server = build_server(get_tools, allowed=("listar_agenda", "ver_datos"))
    try:
        assert {t.name for t in await server.list_tools()} == {"listar_agenda", "ver_datos"}
    finally:
        await conn.close()


async def test_workspace_tools_callable_over_server(tmp_path):
    from ari.infrastructure.workspace.user_workspace import Workspaces
    conn = await connect(":memory:", embedding_dim=4)
    tools = AriTools(actor_from_env(ENV), schedule=SqliteScheduleStore(conn),
                     memory=SqliteMemoryAdapter(conn, embedding_dim=4),
                     turn_log=SqliteTurnLog(conn), tz=TZ, max_items=20, clock=lambda: NOW,
                     workspaces=Workspaces(str(tmp_path)))

    async def get_tools():
        return tools

    server = build_server(get_tools)
    try:
        names = {t.name for t in await server.list_tools()}
        assert {"escribir_archivo", "leer_archivo", "listar_archivos",
                "borrar_archivo", "consultar_sql", "ejecutar"} <= names
        await server.call_tool("escribir_archivo", {"ruta": "a.txt", "contenido": "hola"})
        read = await server.call_tool("leer_archivo", {"ruta": "a.txt"})
        assert read.content[0].text == "hola"
    finally:
        await conn.close()


async def test_ejecutar_registered_only_for_owner_chat():
    async def get_tools():
        raise AssertionError("not called")

    from ari.domain.tools.ari_permissions import allowed_ari_tools
    owner = {t.name for t in await build_server(get_tools, allowed_ari_tools(True, "chat")).list_tools()}
    user = {t.name for t in await build_server(get_tools, allowed_ari_tools(False, "chat")).list_tools()}
    assert "ejecutar" in owner and "ejecutar" not in user


async def test_abrir_archivo_registered_only_for_owner_chat():
    async def get_tools():
        raise AssertionError("not called")
    from ari.domain.tools.ari_permissions import allowed_ari_tools
    owner = {t.name for t in await build_server(get_tools, allowed_ari_tools(True, "chat")).list_tools()}
    user = {t.name for t in await build_server(get_tools, allowed_ari_tools(False, "chat")).list_tools()}
    assert "abrir_archivo" in owner and "abrir_archivo" not in user


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


async def test_email_reading_tools_are_registered_and_delegate():
    """buscar_correos and leer_correo are exposed by build_server and delegate to AriTools."""
    calls: dict[str, object] = {}

    class FakeTools:
        async def buscar_correos(self, cuenta: str, criterio: str = "", limite: int = 10) -> str:
            calls["buscar"] = (cuenta, criterio, limite)
            return "ok-buscar"

        async def leer_correo(self, cuenta: str, id: str) -> str:
            calls["leer"] = (cuenta, id)
            return "ok-leer"

    fake = FakeTools()

    async def get_tools():
        return fake

    server = build_server(get_tools)
    names = {t.name for t in await server.list_tools()}
    assert {"buscar_correos", "leer_correo"} <= names

    result_buscar = await server.call_tool("buscar_correos", {"cuenta": "corp", "criterio": "asunto:hola", "limite": 5})
    assert result_buscar.content[0].text == "ok-buscar"
    assert calls["buscar"] == ("corp", "asunto:hola", 5)

    result_leer = await server.call_tool("leer_correo", {"cuenta": "corp", "id": "42"})
    assert result_leer.content[0].text == "ok-leer"
    assert calls["leer"] == ("corp", "42")
