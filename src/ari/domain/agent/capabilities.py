"""Single source of truth for what Ari can do.

Feeds both Ari's own prompt ("Tus capacidades") and Telegram's "/" menu, so a
feature added here is known to Ari and shown to users at once. When a feature
lands, add it to CAPABILITIES and drop whatever it resolves from LIMITATIONS.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    summary: str  # what it does, told to Ari
    command: str | None = None  # Telegram command, without "/"
    menu: str = ""  # short text for Telegram's menu
    usage: str = ""  # how to use it, told to Ari
    owner_only: bool = False


CAPABILITIES: list[Capability] = [
    Capability(
        command="start", menu="Empezar / pedir acceso",
        summary="Saludo inicial. A quien todavía no tiene acceso le entrega un código "
                "para que el creador lo apruebe."),
    Capability(
        summary="Conversar y ayudar con cualquier tarea recordando a cada usuario: "
                "mensajes recientes, recuerdos de conversaciones pasadas (búsqueda "
                "semántica), datos estables del usuario y un resumen de lo anterior."),
    Capability(
        summary="Mientras trabajas, el usuario ve «escribiendo…» y un mensaje de progreso "
                "con lo que vas haciendo; se borra al llegar tu respuesta final."),
    Capability(
        command="code", menu="Tarea de código: /code [dir:ruta] instrucción", owner_only=True,
        summary="Programar en los proyectos de la carpeta permitida usando Claude Code "
                "con herramientas (leer, editar, ejecutar comandos).",
        usage="/code [dir:ruta] instrucción → propones un plan; si el creador responde "
              "«dale», lo ejecutas en un branch nuevo ari/tg-… con commits. Push, PR y "
              "merge quedan para el creador."),
    Capability(
        command="aprobar", menu="Aprobar acceso: /aprobar CÓDIGO", owner_only=True,
        summary="Aprobar el acceso de un usuario nuevo con su código.",
        usage="/aprobar CÓDIGO"),
    Capability(
        command="revocar", menu="Quitar acceso: /revocar USER_ID", owner_only=True,
        summary="Quitar el acceso a un usuario.", usage="/revocar USER_ID"),
    Capability(
        command="accesos", menu="Ver solicitudes y accesos", owner_only=True,
        summary="Listar las solicitudes de acceso pendientes y los usuarios aprobados."),
    Capability(
        command="restart", menu="Reiniciar Ari", owner_only=True,
        summary="Reiniciarte (y cargar código nuevo). Pide confirmación con «dale»."),
    Capability(
        command="stop", menu="Apagar Ari", owner_only=True,
        summary="Apagarte. Pide confirmación con «dale»; para volver hay que "
                "levantarte desde la máquina."),
]

# What Ari can NOT do yet — so it never promises it, and can propose how to get it.
LIMITATIONS: list[str] = [
    ("En la conversación no tienes herramientas: no puedes navegar la web, leer "
     "archivos ni ejecutar comandos."),
    ("Todavía no tienes conexiones MCP ni integraciones (correo, calendario, "
     "bases de datos, APIs externas). Una conexión nueva solo existe cuando tu creador "
     "la agrega a Ari: autorizar una cuenta en otra app (claude.ai, Google…) NO te da "
     "acceso. Explica qué habría que agregarte, pero no digas que podrás usarlo "
     "hasta que esté agregado."),
    "No puedes escribir por iniciativa propia: solo respondes cuando te escriben.",
    "No puedes programar recordatorios ni tareas periódicas.",
]


def _visible(is_owner: bool) -> list[Capability]:
    return [c for c in CAPABILITIES if is_owner or not c.owner_only]


def menu_commands(owner: bool) -> list[tuple[str, str]]:
    return [(c.command, c.menu) for c in _visible(owner) if c.command]


def render_capabilities(is_owner: bool) -> str:
    lines = ["## Tus capacidades"]
    for cap in _visible(is_owner):
        head = f"- /{cap.command}: " if cap.command else "- "
        lines.append(head + cap.summary + (f" Uso: {cap.usage}" if cap.usage else ""))
    lines += ["", "## Limitaciones actuales (no las prometas; propone cómo resolverlas)"]
    lines += [f"- {limit}" for limit in LIMITATIONS]
    return "\n".join(lines)
