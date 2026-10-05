"""Ari's own MCP server (stdio). Launched by the Claude CLI once per turn; the
actor, role and context come only from the env Ari wrote in the turn config."""
from collections.abc import Awaitable, Callable, Iterable, Mapping

from mcp.server.mcpserver import MCPServer

from ari.application.ari_tools import Actor, AriTools
from ari.domain.tools.ari_permissions import ARI_TOOLS

_REQUIRED = ("ARI_ACTOR_ID", "ARI_ACTOR_CHAT", "ARI_ROLE", "ARI_CONTEXT", "ARI_TURN_ID")


def actor_from_env(env: Mapping[str, str]) -> Actor:
    missing = [k for k in _REQUIRED if not env.get(k)]
    if missing:
        raise RuntimeError(f"Ari MCP server started without {', '.join(missing)}")
    return Actor(env["ARI_ACTOR_ID"], env["ARI_ACTOR_CHAT"], env.get("ARI_ACTOR_NAME", ""),
                 env["ARI_ROLE"] == "owner", env["ARI_CONTEXT"], env["ARI_TURN_ID"])


def build_server(get_tools: Callable[[], Awaitable[AriTools]],
                 allowed: Iterable[str] | None = None) -> MCPServer:
    """With ``allowed`` given, only those tool names are registered (used for a
    per-turn server whose actor env pins the context/role in advance); ``None``
    (a bare handshake, no actor env yet) registers the full catalogue."""
    server = MCPServer("ari")
    names = set(ARI_TOOLS) if allowed is None else set(allowed)

    def tool(name: str):
        return server.tool() if name in names else (lambda fn: fn)

    @tool("agendar")
    async def agendar(tipo: str, texto: str, at: str | None = None,
                      cron: str | None = None, de_usuario: str | None = None) -> str:
        """Agenda un recordatorio o una tarea. tipo: "recordatorio" o "tarea".
        Usa at (ISO local, p. ej. 2026-09-26T09:00) para una vez, o cron (5 campos,
        hora local, mínimo cada 1 hora) para repetir. de_usuario: opcional, @usuario
        o id de otra persona que te compartió sus recordatorios (lo agendas en su
        agenda y le llega a ella)."""
        return await (await get_tools()).agendar(tipo, texto, at, cron, de_usuario)

    @tool("listar_agenda")
    async def listar_agenda(de_usuario: str | None = None) -> str:
        """Lista los recordatorios y tareas activos con su #número. de_usuario:
        opcional, @usuario o id de alguien que te compartió sus recordatorios para
        ver los de esa persona."""
        return await (await get_tools()).listar_agenda(de_usuario)

    @tool("cancelar")
    async def cancelar(id: int, de_usuario: str | None = None) -> str:
        """Cancela un recordatorio o tarea por su #número. de_usuario: opcional,
        @usuario o id de alguien que te compartió sus recordatorios con permiso de
        gestión, para cancelar uno de esa persona."""
        return await (await get_tools()).cancelar(id, de_usuario)

    @tool("recordar_dato")
    async def recordar_dato(clave: str, valor: str) -> str:
        """Guarda o corrige un dato estable del usuario (p. ej. clave "hija", valor "Ana")."""
        return await (await get_tools()).recordar_dato(clave, valor)

    @tool("olvidar_dato")
    async def olvidar_dato(clave: str) -> str:
        """Borra un dato guardado del usuario por su clave."""
        return await (await get_tools()).olvidar_dato(clave)

    @tool("ver_datos")
    async def ver_datos() -> str:
        """Muestra los datos guardados del usuario."""
        return await (await get_tools()).ver_datos()

    @tool("aprobar_acceso")
    async def aprobar_acceso(codigo_o_usuario: str) -> str:
        """(Solo el creador) Aprueba una solicitud de acceso por su código o por @usuario."""
        return await (await get_tools()).aprobar_acceso(codigo_o_usuario)

    @tool("revocar_acceso")
    async def revocar_acceso(usuario: str) -> str:
        """(Solo el creador) Quita el acceso a un usuario por @usuario o id."""
        return await (await get_tools()).revocar_acceso(usuario)

    @tool("ver_accesos")
    async def ver_accesos() -> str:
        """(Solo el creador) Lista las solicitudes pendientes y los usuarios aprobados."""
        return await (await get_tools()).ver_accesos()

    @tool("enviar_mensaje")
    async def enviar_mensaje(destinatario: str, texto: str) -> str:
        """(Solo el creador) Envía un mensaje firmado a un usuario aprobado, por @usuario o id."""
        return await (await get_tools()).enviar_mensaje(destinatario, texto)

    @tool("proponer_codigo")
    async def proponer_codigo(instruccion: str, carpeta: str | None = None) -> str:
        """(Solo el creador) Prepara un plan para implementar, programar, arreglar o
        cambiar código. Llámala directamente SIEMPRE que tu creador te pida en lenguaje
        natural implementar, programar, arreglar o cambiar algo (o acepte tu sugerencia):
        es la forma de arrancar una tarea de código; no le pidas que escriba ningún
        comando. Solo prepara el plan: nada se modifica hasta que tu creador responda
        «dale». carpeta: opcional, relativa a la carpeta permitida (por defecto, el
        repositorio de Ari)."""
        return await (await get_tools()).proponer_codigo(instruccion, carpeta)

    @tool("proponer_comando")
    async def proponer_comando(comando: str) -> str:
        """(Solo el creador) Prepara un comando de terminal para ejecutar en la máquina
        de tu creador. Llámala SOLO cuando tu creador te pida explícitamente correr algo
        en la terminal/consola/shell (p. ej. «corré los tests», «mostrame el git status»,
        «qué procesos hay», «instalá tal paquete»); no le pidas que escriba ningún comando
        especial. NUNCA la uses por tu cuenta para avanzar una tarea, inspeccionar archivos
        o conseguir datos: si te falta algo, dilo con palabras. Solo prepara el comando:
        nada se ejecuta hasta que tu creador responda «dale». comando: la línea de shell
        exacta a correr."""
        return await (await get_tools()).proponer_comando(comando)

    @tool("asignar_mision")
    async def asignar_mision(instruccion: str) -> str:
        """Crea una misión de fondo: Ari la ejecuta de forma autónoma, se demore lo que
        se demore, y avisa al usuario cuando termina. Úsala cuando el usuario pide algo
        que puede tardar: buscar información, analizar datos, preparar un resumen largo.
        La misión se reintenta hasta 3 veces si falla."""
        return await (await get_tools()).asignar_mision(instruccion)

    @tool("ver_misiones")
    async def ver_misiones() -> str:
        """Muestra el estado de las misiones del usuario: pendientes, en curso, pausadas."""
        return await (await get_tools()).ver_misiones()

    @tool("cancelar_mision")
    async def cancelar_mision(id: int) -> str:
        """Cancela una misión pendiente o en curso por su #número."""
        return await (await get_tools()).cancelar_mision(id)

    @tool("pedir_credenciales")
    async def pedir_credenciales(nombres: str) -> str:
        """(Solo el creador) Cuando tu creador quiera darte una credencial, API o
        contraseña, prepara un link seguro para que la cargue sin escribirla en el chat.
        nombres: indica el nombre de la variable en UPPER_SNAKE (p. ej. NOTION_API_KEY);
        si son varias, sepáralas con comas (p. ej. NOTION_API_KEY, GITHUB_TOKEN). Usar el
        nombre exacto de la variable permite registrar credenciales nuevas en el formulario."""
        return await (await get_tools()).pedir_credenciales(nombres)

    @tool("ver_skills")
    async def ver_skills() -> str:
        """(Solo el creador) Lista los skills instalados con su estado (on/off) y descripción."""
        return await (await get_tools()).ver_skills()

    @tool("activar_skill")
    async def activar_skill(nombre: str) -> str:
        """(Solo el creador) Activa un skill instalado por su nombre exacto."""
        return await (await get_tools()).activar_skill(nombre)

    @tool("desactivar_skill")
    async def desactivar_skill(nombre: str) -> str:
        """(Solo el creador) Desactiva un skill instalado por su nombre exacto."""
        return await (await get_tools()).desactivar_skill(nombre)

    @tool("compartir")
    async def compartir(capacidad: str, usuario: str, nivel: str = "act") -> str:
        """Comparte una capacidad (p. ej. «recordatorios») con otro usuario aprobado.
        usuario: @username o id. nivel: «act» (ver y gestionar, por defecto) o «read»
        (solo ver). El destinatario recibe una notificación automática."""
        return await (await get_tools()).compartir(capacidad, usuario, nivel)

    @tool("ver_permisos")
    async def ver_permisos() -> str:
        """Lista los permisos que le diste a otros usuarios y los que otros te dieron."""
        return await (await get_tools()).ver_permisos()

    @tool("revocar_permiso")
    async def revocar_permiso(capacidad: str, usuario: str) -> str:
        """Revoca el permiso que le diste a un usuario sobre una capacidad.
        usuario: @username o id. El usuario pierde acceso de inmediato."""
        return await (await get_tools()).revocar_permiso(capacidad, usuario)

    @tool("conectar_correo")
    async def conectar_correo() -> str:
        """Conecta una casilla de correo del usuario (IMAP/SMTP). Te mando un archivo
        para cargar los datos de forma segura; el usuario pega de vuelta un código."""
        return await (await get_tools()).conectar_correo()

    @tool("mis_correos")
    async def mis_correos() -> str:
        """Lista las casillas de correo que el usuario tiene conectadas (sin mostrar
        la contraseña)."""
        return await (await get_tools()).mis_correos()

    @tool("olvidar_correo")
    async def olvidar_correo(label: str) -> str:
        """Desconecta una casilla de correo del usuario por su etiqueta."""
        return await (await get_tools()).olvidar_correo(label)

    @tool("escribir_archivo")
    async def escribir_archivo(ruta: str, contenido: str) -> str:
        """Crea o sobrescribe un archivo de texto en tu espacio de trabajo privado.
        ruta es relativa, p. ej. "notas/plan.txt"."""
        return await (await get_tools()).escribir_archivo(ruta, contenido)

    @tool("leer_archivo")
    async def leer_archivo(ruta: str) -> str:
        """Lee un archivo de texto de tu espacio de trabajo privado."""
        return await (await get_tools()).leer_archivo(ruta)

    @tool("listar_archivos")
    async def listar_archivos(ruta: str = ".") -> str:
        """Lista los archivos y carpetas de tu espacio de trabajo privado."""
        return await (await get_tools()).listar_archivos(ruta)

    @tool("borrar_archivo")
    async def borrar_archivo(ruta: str) -> str:
        """Borra un archivo de tu espacio de trabajo privado."""
        return await (await get_tools()).borrar_archivo(ruta)

    @tool("consultar_sql")
    async def consultar_sql(base: str, sql: str) -> str:
        """Ejecuta UNA sentencia SQL sobre un archivo .sqlite de tu espacio de
        trabajo (crear tablas, insertar, consultar). base es el nombre del
        archivo, p. ej. "datos.sqlite"."""
        return await (await get_tools()).consultar_sql(base, sql)

    @tool("ejecutar")
    async def ejecutar(comando: str) -> str:
        """(Solo dueño) Ejecuta un comando de shell con el directorio de trabajo
        en tu espacio privado. Devuelve código de salida, stdout y stderr."""
        return await (await get_tools()).ejecutar(comando)

    @tool("leer_documento")
    async def leer_documento(ruta: str) -> str:
        """Lee y extrae el texto de un documento de tu espacio de trabajo
        (PDF, Excel .xlsx, CSV o .txt). ruta es relativa, p. ej. "facturas/sweetcoffee.pdf"."""
        return await (await get_tools()).leer_documento(ruta)

    @tool("abrir_archivo")
    async def abrir_archivo(ruta: str) -> str:
        """(Solo dueño) Prepara un link seguro en la red local (un solo uso, vence
        pronto) para abrir un archivo de la Mac en el navegador. ruta: la ruta del
        archivo (p. ej. "/Users/.../Desktop/factura.pdf"). No comparte secretos
        (vault, .env, ~/.ssh, llaves)."""
        return await (await get_tools()).abrir_archivo(ruta)

    return server
