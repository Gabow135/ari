"""Single source of truth for what Ari can do.

Feeds both Ari's own prompt ("Tus capacidades") and Telegram's "/" menu, so a
feature added here is known to Ari and shown to users at once. When a feature
lands, add it to CAPABILITIES and drop whatever it resolves from `limitations()`.
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
        command="recordatorios", menu="Ver tus recordatorios y tareas",
        summary="Recordatorios y tareas programadas pedidas en lenguaje natural "
                "(«recuérdame mañana a las 9…», «cada lunes a las 8 resúmeme…»), "
                "puntuales o recurrentes, con tus herramientas agendar, listar_agenda "
                "y cancelar.",
        usage="/recordatorios lista los activos con su #número."),
    Capability(
        summary="Memoria explícita: guardas, corriges y borras datos del usuario cuando te "
                "lo pide («recuerda que…», «olvida…», «¿qué sabes de mí?») con "
                "recordar_dato, olvidar_dato y ver_datos."),
    Capability(
        owner_only=True,
        summary="Administrar accesos conversando («aprueba a @juan», «revoca a @pedro», "
                "«¿quién pidió acceso?») con aprobar_acceso, revocar_acceso y ver_accesos."),
    Capability(
        owner_only=True,
        summary="Enviar mensajes a usuarios aprobados («avísale a @juan que…») con "
                "enviar_mensaje; llegan firmados con el nombre de tu creador."),
    Capability(
        owner_only=True,
        summary="Latido cada hora (fuera del horario de silencio 22–07): revisas por tu "
                "cuenta si hay algo útil que decirle a tu creador; si no, no escribes."),
    Capability(
        owner_only=True,
        summary="Avisos del sistema a tu creador: solicitudes de acceso sin aprobar hace "
                "más de 12 h, fallas repetidas de Claude y tareas pausadas."),
    Capability(
        command="conexiones", menu="Ver conexiones (MCP y web)", owner_only=True,
        summary="Ver el estado de tus conexiones MCP y de la web: cuáles están "
                "configuradas y qué falta en las que no."),
    Capability(
        command="vault", menu="Cargar credenciales (link seguro)", owner_only=True,
        summary="Genera un link HTTPS de un solo uso (vence pronto) para cargar "
                "credenciales MCP en la bóveda cifrada desde el navegador, sin terminal.",
        usage="/vault → Ari te manda el link; ábrelo en la misma red y carga los valores."),
    Capability(
        command="code", menu="Tarea de código: /code [dir:ruta] instrucción", owner_only=True,
        summary="Programar en los proyectos de la carpeta permitida usando Claude Code "
                "con herramientas (leer, editar, ejecutar comandos). El creador NO "
                "necesita escribir /code: basta con que te pida el cambio en lenguaje "
                "natural y tú preparas el plan con proponer_codigo. /code es solo un "
                "atajo explícito opcional.",
        usage="con o sin /code, propones un plan; si el creador responde «dale», lo "
              "ejecutas en un branch nuevo ari/tg-… con commits. Push, PR y merge "
              "quedan para el creador."),
    Capability(
        owner_only=True,
        summary="Cuando tu creador te pide en lenguaje natural implementar, programar, "
                "arreglar o cambiar código (o acepta tu sugerencia), llamas a "
                "proponer_codigo directamente —sin pedirle que escriba /code— para "
                "preparar el plan; se ejecuta solo con su «dale»."),
    Capability(
        owner_only=True,
        summary="Correr comandos de terminal en la máquina de tu creador. Cuando te pida "
                "en lenguaje natural ejecutar algo en la shell/consola («corré los tests», "
                "«mostrame el git status», «qué procesos hay»), llamas a proponer_comando "
                "con la línea exacta; el comando se ejecuta solo con su «dale» y le devuelves "
                "la salida. No le pidas que escriba ningún comando especial."),
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
    Capability(
        command="skills", menu="Ver skills", owner_only=True,
        summary="Lista los skills instalados y su estado (activo, falta credencial, etc.).",
        usage="/skills"),
    Capability(
        command="skill_on", menu="Activar skill", owner_only=True,
        summary="Activa un skill; si le falta una credencial, Ari te manda un link seguro para cargarla.",
        usage="/skill_on <nombre>"),
    Capability(
        command="skill_off", menu="Desactivar skill", owner_only=True,
        summary="Desactiva un skill sin reiniciar a Ari.",
        usage="/skill_off <nombre>"),
]

# What Ari can NOT do yet — so it never promises it, and can propose how to get it.
_NO_TOOLS = ("En la conversación no tienes herramientas directas: no puedes navegar la "
             "web ni leer archivos por tu cuenta en el momento.")
_NO_MCP = ("Todavía no tienes conexiones MCP ni integraciones (correo, calendario, "
           "bases de datos, APIs externas). Una conexión nueva solo existe cuando tu creador "
           "la agrega a Ari: autorizar una cuenta en otra app (claude.ai, Google…) NO te da "
           "acceso. Explica qué habría que agregarte, pero no digas que podrás usarlo "
           "hasta que esté agregado.")
_ONLY_LISTED = ("Solo tienes las conexiones listadas en «Tus herramientas y conexiones». "
                "Una conexión nueva solo existe cuando tu creador la agrega a Ari "
                "(mcp/servers.json); autorizar una cuenta en otra app NO te da acceso.")
_DEGRADED_NOTE = ("Tienes conexiones configuradas pero inactivas (ver sección "
                  "«Conexiones degradadas» en «Tus herramientas»). Puedes mencionarlas "
                  "proactivamente y guiar a tu creador a activarlas con /vault o "
                  "revisando la configuración. No las uses hasta que estén activas.")


def limitations(has_web: bool, has_mcp: bool, has_degraded: bool = False) -> list[str]:
    """What Ari can NOT do, given the tools of the current conversation."""
    out = []
    if not has_web:
        out.append(_NO_TOOLS)
    out.append(_ONLY_LISTED if has_mcp else _NO_MCP)
    if has_degraded:
        out.append(_DEGRADED_NOTE)
    return out


# Without tools (e.g. memory maintenance, or no tool policy wired).
LIMITATIONS: list[str] = limitations(False, False)


def _visible(is_owner: bool) -> list[Capability]:
    return [c for c in CAPABILITIES if is_owner or not c.owner_only]


def menu_commands(owner: bool) -> list[tuple[str, str]]:
    return [(c.command, c.menu) for c in _visible(owner) if c.command]


def render_capabilities(is_owner: bool, has_web: bool = False, has_mcp: bool = False,
                        has_degraded: bool = False) -> str:
    lines = ["## Tus capacidades"]
    for cap in _visible(is_owner):
        head = f"- /{cap.command}: " if cap.command else "- "
        lines.append(head + cap.summary + (f" Uso: {cap.usage}" if cap.usage else ""))
    lines += ["", "## Limitaciones actuales (no las prometas; propone cómo resolverlas)"]
    lines += [f"- {limit}" for limit in limitations(has_web, has_mcp, has_degraded)]
    return "\n".join(lines)
