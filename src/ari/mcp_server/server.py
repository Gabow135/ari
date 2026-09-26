"""Ari's own MCP server (stdio). Launched by the Claude CLI once per turn; the
actor, role and context come only from the env Ari wrote in the turn config."""
from collections.abc import Awaitable, Callable, Mapping

from mcp.server.mcpserver import MCPServer

from ari.application.ari_tools import Actor, AriTools

_REQUIRED = ("ARI_ACTOR_ID", "ARI_ACTOR_CHAT", "ARI_ROLE", "ARI_CONTEXT", "ARI_TURN_ID")


def actor_from_env(env: Mapping[str, str]) -> Actor:
    missing = [k for k in _REQUIRED if not env.get(k)]
    if missing:
        raise RuntimeError(f"Ari MCP server started without {', '.join(missing)}")
    return Actor(env["ARI_ACTOR_ID"], env["ARI_ACTOR_CHAT"], env.get("ARI_ACTOR_NAME", ""),
                 env["ARI_ROLE"] == "owner", env["ARI_CONTEXT"], env["ARI_TURN_ID"])


def build_server(get_tools: Callable[[], Awaitable[AriTools]]) -> MCPServer:
    server = MCPServer("ari")

    @server.tool()
    async def agendar(tipo: str, texto: str, at: str | None = None,
                      cron: str | None = None) -> str:
        """Agenda un recordatorio (se envía el texto a la hora indicada) o una tarea
        (a esa hora ejecutas la instrucción). tipo: "recordatorio" o "tarea". Usa
        at (fecha y hora local ISO, p. ej. 2026-09-26T09:00) para una vez, o cron
        (5 campos, hora local, mínimo cada 1 hora) para repetir."""
        return await (await get_tools()).agendar(tipo, texto, at, cron)

    @server.tool()
    async def listar_agenda() -> str:
        """Lista los recordatorios y tareas activos del usuario con su #número."""
        return await (await get_tools()).listar_agenda()

    @server.tool()
    async def cancelar(id: int) -> str:
        """Cancela un recordatorio o tarea del usuario por su #número."""
        return await (await get_tools()).cancelar(id)

    @server.tool()
    async def recordar_dato(clave: str, valor: str) -> str:
        """Guarda o corrige un dato estable del usuario (p. ej. clave "hija", valor "Ana")."""
        return await (await get_tools()).recordar_dato(clave, valor)

    @server.tool()
    async def olvidar_dato(clave: str) -> str:
        """Borra un dato guardado del usuario por su clave."""
        return await (await get_tools()).olvidar_dato(clave)

    @server.tool()
    async def ver_datos() -> str:
        """Muestra los datos guardados del usuario."""
        return await (await get_tools()).ver_datos()

    @server.tool()
    async def aprobar_acceso(codigo_o_usuario: str) -> str:
        """(Solo el creador) Aprueba una solicitud de acceso por su código o por @usuario."""
        return await (await get_tools()).aprobar_acceso(codigo_o_usuario)

    @server.tool()
    async def revocar_acceso(usuario: str) -> str:
        """(Solo el creador) Quita el acceso a un usuario por @usuario o id."""
        return await (await get_tools()).revocar_acceso(usuario)

    @server.tool()
    async def ver_accesos() -> str:
        """(Solo el creador) Lista las solicitudes pendientes y los usuarios aprobados."""
        return await (await get_tools()).ver_accesos()

    @server.tool()
    async def enviar_mensaje(destinatario: str, texto: str) -> str:
        """(Solo el creador) Envía un mensaje firmado a un usuario aprobado, por @usuario o id."""
        return await (await get_tools()).enviar_mensaje(destinatario, texto)

    return server
