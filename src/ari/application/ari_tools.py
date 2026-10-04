"""Logic behind Ari's own MCP tools. Every method acts for the Actor given by
Ari (never chosen by the model), checks the permission table, validates with the
domain rules, and records a code-generated receipt for each change."""
import logging
from dataclasses import dataclass

from ari.application.access.gate import normalize_code
from ari.application.schedule.agenda_format import created_receipt, item_line
from ari.application.text_format import truncate
from ari.domain.access.entities import APPROVED, PENDING
from ari.domain.grants.entities import ACT as GRANT_ACT
from ari.domain.grants.entities import READ as GRANT_READ
from ari.domain.memory.fact_keys import normalize_key
from ari.domain.schedule.actions import ActionError, next_cron_run, parse_action
from ari.domain.schedule.entities import ACTIVE, CANCELLED, PAUSED, REMINDER, RUNNING, TASK
from ari.domain.tools.ari_permissions import allowed_ari_tools

log = logging.getLogger("ari.tools")

DENIED = "No permitido en este contexto."
_KINDS = {"recordatorio": REMINDER, "tarea": TASK}
SCHEDULE = "schedule"
_CAPS = {"recordatorios": SCHEDULE, "recordatorio": SCHEDULE,
         "tareas": SCHEDULE, "tarea": SCHEDULE, "agenda": SCHEDULE}


def _who(rec) -> str:
    return f"@{rec.username} (id {rec.user_id})" if rec.username else f"id {rec.user_id}"


def _match(records, arg: str):
    """Records matching '@username' (case-insensitive) or a numeric id."""
    target = (arg or "").strip()
    if target.startswith("@"):
        name = target[1:].lower()
        if not name:  # bare "@" must not match users without a username
            return []
        return [r for r in records if (r.username or "").lower() == name]
    return [r for r in records if r.user_id == target]


@dataclass(frozen=True)
class Actor:
    user_id: str
    chat_id: str
    name: str
    is_owner: bool
    context: str
    turn_id: str


class AriTools:
    def __init__(self, actor: Actor, *, schedule, memory, turn_log, tz, max_items: int,
                 clock, gate=None, access=None, coding=None, commands=None, missions=None,
                 credentials=None, skills=None, grants=None):
        self._a, self._schedule, self._memory, self._log = actor, schedule, memory, turn_log
        self._tz, self._max, self._clock = tz, max_items, clock
        self._gate, self._access = gate, access
        self._coding = coding  # SqliteCodingRequests | None
        self._commands = commands  # SqliteCommandRequests | None
        self._missions = missions  # SqliteMissions | None
        self._credentials = credentials  # SqliteCredentialRequests | None
        self._skills = skills  # SkillManager (load=False) | None
        self._grants = grants  # GrantPolicy | None

    def _allowed(self, tool: str) -> bool:
        if tool in allowed_ari_tools(self._a.is_owner, self._a.context):
            return True
        log.warning("denied %s for %s in context %s", tool, self._a.user_id, self._a.context)
        return False

    async def _receipt(self, text: str) -> str:
        await self._log.add_receipt(self._a.turn_id, text)
        return text

    # ---- agenda -----------------------------------------------------------

    async def _target(self, de_usuario: str, min_level: str) -> tuple[str | None, str | None]:
        """Resolve an @usuario/id and check a 'schedule' grant from them to the
        actor. Returns (target_user_id, None) when allowed, else (None, error)."""
        if self._grants is None or self._access is None:
            return None, "El sistema de permisos no está disponible."
        rec, error = await self._resolve(de_usuario, {APPROVED})
        if error:
            return None, error
        if not await self._grants.allows(self._a.user_id, rec.user_id, SCHEDULE, min_level):
            verb = "ver" if min_level == GRANT_READ else "gestionar"
            return None, f"No tienes permiso para {verb} los recordatorios de {_who(rec)}."
        return rec.user_id, None

    async def agendar(self, tipo: str, texto: str, at: str | None = None,
                      cron: str | None = None, de_usuario: str | None = None) -> str:
        if not self._allowed("agendar"):
            return DENIED
        uid, chat = self._a.user_id, self._a.chat_id
        if de_usuario:
            uid, error = await self._target(de_usuario, GRANT_ACT)
            if error:
                return error
            chat = uid  # DM assistant: the owner's reminder is delivered to them
        kind = _KINDS.get((tipo or "").strip().lower())
        if kind is None:
            return "No pude agendarlo: el tipo debe ser «recordatorio» o «tarea»."
        now = self._clock()
        try:
            action = parse_action({"type": kind, "text": texto, "at": at, "cron": cron},
                                  now, self._tz)
        except ActionError as exc:
            return f"No pude agendarlo: {exc}."
        if await self._schedule.count_active(uid) >= self._max:
            return (f"No pude agendarlo: ya hay {self._max} recordatorios o tareas "
                    "activos. Cancela alguno primero.")
        next_run = action.at or next_cron_run(action.cron, now, self._tz)
        item_id = await self._schedule.add(uid, chat, action.kind,
                                           action.text, next_run, action.cron)
        return await self._receipt(created_receipt(item_id, action.kind, action.text,
                                                   next_run, action.cron, self._tz))

    async def listar_agenda(self, de_usuario: str | None = None) -> str:
        if not self._allowed("listar_agenda"):
            return DENIED
        uid = self._a.user_id
        if de_usuario:
            uid, error = await self._target(de_usuario, GRANT_READ)
            if error:
                return error
        items = await self._schedule.list_for_user(uid)
        if not items:
            return "No tienes recordatorios ni tareas activos."
        return "\n".join(item_line(i, self._tz) for i in items)

    async def cancelar(self, id: int, de_usuario: str | None = None) -> str:
        if not self._allowed("cancelar"):
            return DENIED
        uid = self._a.user_id
        if de_usuario:
            uid, error = await self._target(de_usuario, GRANT_ACT)
            if error:
                return error
        try:
            id_int = int(id)
        except (ValueError, TypeError):
            return f"No encontré el #{id} entre los recordatorios."
        item = await self._schedule.get(id_int)
        if (item is None or item.user_id != uid
                or item.status not in (ACTIVE, PAUSED, RUNNING)):
            return f"No encontré el #{id_int} entre los recordatorios."
        await self._schedule.set_status(item.id, CANCELLED)
        return await self._receipt(f"🗑️ Cancelado #{item.id}: {item.text}")

    # ---- explicit memory --------------------------------------------------

    async def recordar_dato(self, clave: str, valor: str) -> str:
        if not self._allowed("recordar_dato"):
            return DENIED
        clave, valor = (clave or "").strip(), (valor or "").strip()
        if not clave or not valor:
            return "No pude guardarlo: falta la clave o el valor."
        if len(clave) > 100 or len(valor) > 500:
            return "No pude guardarlo: es demasiado largo."
        normalized = normalize_key(clave)
        old = await self._memory.get_fact(self._a.user_id, normalized)
        await self._memory.upsert_fact(self._a.user_id, normalized, valor)
        if old is not None and old.value != valor:
            await self._memory.add_fact_history(
                self._a.user_id, normalized, old.value, valor, "manual"
            )
            return await self._receipt(f"🧠 Actualicé: {clave} = {valor} (antes: {old.value})")
        old_value = old.value if old is not None else None
        await self._memory.add_fact_history(
            self._a.user_id, normalized, old_value, valor, "manual"
        )
        return await self._receipt(f"🧠 Guardé: {clave} = {valor}")

    async def olvidar_dato(self, clave: str) -> str:
        if not self._allowed("olvidar_dato"):
            return DENIED
        clave = (clave or "").strip()
        normalized = normalize_key(clave)
        if not await self._memory.delete_fact(self._a.user_id, normalized):
            return f"No tenía guardado «{clave}»."
        return await self._receipt(f"🧹 Olvidé: {clave}")

    async def ver_datos(self) -> str:
        if not self._allowed("ver_datos"):
            return DENIED
        facts = await self._memory.get_facts(self._a.user_id)
        if not facts:
            return "No tengo datos guardados de ti."
        return "\n".join(f"- {f.key}: {f.value}" for f in facts)

    # ---- cross-user grants ------------------------------------------------

    @staticmethod
    def _level(nivel: str) -> str:
        return GRANT_ACT if (nivel or "").strip().lower() in (
            "act", "accion", "acción", "gestionar", "escribir") else GRANT_READ

    async def compartir(self, capacidad: str, usuario: str, nivel: str = "act") -> str:
        if not self._allowed("compartir"):
            return DENIED
        if self._grants is None or self._access is None:
            return "No puedo compartir: el sistema de permisos no está disponible."
        cap = _CAPS.get((capacidad or "").strip().lower())
        if cap is None:
            return "No pude compartir eso: por ahora solo puedo compartir «recordatorios»."
        rec, error = await self._resolve(usuario, {APPROVED})
        if error:
            return error
        if rec.user_id == self._a.user_id:
            return "No puedes compartir tus recordatorios contigo mismo."
        level = self._level(nivel)
        await self._grants.share(self._a.user_id, rec.user_id, cap, level)
        nivel_txt = "ver y gestionar" if level == GRANT_ACT else "ver"
        signature = (self._a.name or "").strip() or "un contacto"
        await self._log.outbox_add(
            rec.user_id,
            f"🔑 {signature} te dio permiso para {nivel_txt} sus recordatorios. "
            "Pídemelos cuando quieras (por ejemplo: «muéstrame los recordatorios de "
            f"{signature}»).")
        return await self._receipt(
            f"🔑 Listo, {_who(rec)} ahora puede {nivel_txt} tus recordatorios.")

    async def ver_permisos(self) -> str:
        if not self._allowed("ver_permisos"):
            return DENIED
        if self._grants is None or self._access is None:
            return "El sistema de permisos no está disponible."
        given = await self._grants.given_by(self._a.user_id)
        received = await self._grants.received_by(self._a.user_id)
        if not given and not received:
            return "No compartiste permisos ni te compartieron ninguno."
        by_id = {r.user_id: r for r in await self._access.list_all()}

        def who(uid: str) -> str:
            rec = by_id.get(uid)
            return _who(rec) if rec else f"id {uid}"

        lines: list[str] = []
        if given:
            lines.append("Diste:")
            lines += [f"  • {who(g.grantee_id)} — recordatorios ({g.level})" for g in given]
        if received:
            lines.append("Te dieron:")
            lines += [f"  • {who(g.grantor_id)} — recordatorios ({g.level})" for g in received]
        return "\n".join(lines)

    async def revocar_permiso(self, capacidad: str, usuario: str) -> str:
        if not self._allowed("revocar_permiso"):
            return DENIED
        if self._grants is None or self._access is None:
            return "El sistema de permisos no está disponible."
        cap = _CAPS.get((capacidad or "").strip().lower())
        if cap is None:
            return "No reconozco esa capacidad. Por ahora solo «recordatorios»."
        rec, error = await self._resolve(usuario, {APPROVED})
        if error:
            return error
        if await self._grants.revoke(self._a.user_id, rec.user_id, cap):
            signature = (self._a.name or "").strip() or "tu contacto"
            await self._log.outbox_add(
                rec.user_id,
                f"🔒 Ya no tienes acceso a los recordatorios de {signature}.")
            return await self._receipt(f"🔒 Revoqué el permiso a {_who(rec)}.")
        return f"{_who(rec)} no tenía permiso sobre tus recordatorios."

    # ---- owner administration --------------------------------------------

    async def _resolve(self, arg: str, statuses: set[str]):
        records = [r for r in await self._access.list_all() if r.status in statuses]
        found = _match(records, arg)
        if not found:
            return None, f"No encontré a {arg or 'ese usuario'}."
        if len(found) > 1:
            return None, f"Hay varios usuarios que coinciden con {arg}; usa su id."
        return found[0], None

    async def _deliver(self, result) -> None:
        for chat_id, text in result.notifications:
            await self._log.outbox_add(chat_id, text)

    async def aprobar_acceso(self, codigo_o_usuario: str) -> str:
        if not self._allowed("aprobar_acceso"):
            return DENIED
        arg = (codigo_o_usuario or "").strip()
        if arg.startswith("@"):
            records = [r for r in await self._access.list_all() if r.status == PENDING]
            found = _match(records, arg)
            if not found:
                return f"No encontré una solicitud pendiente de {arg}."
            if len(found) > 1:
                return f"Hay varias solicitudes de {arg}; usa el código."
            code = found[0].code
        else:
            code = normalize_code(arg)
        result = await self._gate.admin_command(f"/aprobar {code}", self._a.user_id)
        await self._deliver(result)
        if result.notifications:  # only a real approval notifies the user
            user_id = result.notifications[0][0]
            approved = await self._access.get(user_id)
            who = _who(approved) if approved else f"id {user_id}"
            await self._receipt(f"✅ Aprobé a {who}")
        return result.reply

    async def revocar_acceso(self, usuario: str) -> str:
        if not self._allowed("revocar_acceso"):
            return DENIED
        rec, error = await self._resolve(usuario, {PENDING, APPROVED})
        if error:
            return error
        if self._gate.is_owner(rec.user_id):
            return "No puedo hacer eso con la cuenta de un creador."
        result = await self._gate.admin_command(f"/revocar {rec.user_id}", self._a.user_id)
        if await self._access.get(rec.user_id) is None:  # the gate really revoked it
            await self._receipt(f"⛔ Revoqué a {_who(rec)}")
        return result.reply

    async def ver_accesos(self) -> str:
        if not self._allowed("ver_accesos"):
            return DENIED
        return (await self._gate.admin_command("/accesos", self._a.user_id)).reply

    async def enviar_mensaje(self, destinatario: str, texto: str) -> str:
        if not self._allowed("enviar_mensaje"):
            return DENIED
        if self._gate is None or not self._gate.is_owner(self._a.user_id):
            return DENIED
        texto = (texto or "").strip()
        if not texto or len(texto) > 1000:
            return "No pude enviarlo: el texto debe tener entre 1 y 1000 caracteres."
        rec, error = await self._resolve(destinatario, {APPROVED})
        if error:
            return error
        if self._gate.is_owner(rec.user_id):
            return "No puedo hacer eso con la cuenta de un creador."
        signature = (self._a.name or "").strip() or "tu contacto"
        await self._log.outbox_add(rec.user_id, f"📨 De {signature} (vía Ari): {texto}")
        return await self._receipt(f"📨 Enviado a {_who(rec)}")

    # ---- missions (background tasks) -------------------------------------

    async def asignar_mision(self, instruccion: str) -> str:
        if not self._allowed("asignar_mision"):
            return DENIED
        instruccion = (instruccion or "").strip()
        if not instruccion or len(instruccion) > 2000:
            return "No pude crearla: la instrucción debe tener entre 1 y 2000 caracteres."
        if self._missions is None:
            return "No pude crearla: el sistema de misiones no está disponible."
        mid = await self._missions.add(self._a.user_id, self._a.chat_id, instruccion)
        return await self._receipt(f"🎯 Misión #{mid} creada: {truncate(instruccion)}")

    async def ver_misiones(self) -> str:
        if not self._allowed("ver_misiones"):
            return DENIED
        if self._missions is None:
            return "No pude verlas: el sistema de misiones no está disponible."
        items = await self._missions.list_for_user(self._a.user_id)
        if not items:
            return "No tienes misiones activas."
        _icons = {"pending": "⏳", "running": "🔄", "done": "✅",
                  "failed": "❌", "paused": "⚠️", "cancelled": "🗑️"}
        lines = []
        for m in items:
            icon = _icons.get(m.status, "❓")
            lines.append(f"{icon} #{m.id} [{m.status}]: {truncate(m.instruction, 80)}")
            if m.result and m.status in ("done", "paused", "failed"):
                lines.append(f"   → {truncate(m.result, 120)}")
        return "\n".join(lines)

    async def cancelar_mision(self, id: int) -> str:
        if not self._allowed("cancelar_mision"):
            return DENIED
        if self._missions is None:
            return "No pude cancelarla: el sistema de misiones no está disponible."
        try:
            mid = int(id)
        except (ValueError, TypeError):
            return f"No encontré la misión #{id}."
        mission = await self._missions.get(mid)
        if mission is None or mission.user_id != self._a.user_id:
            return f"No encontré la misión #{mid}."
        if mission.status in ("done", "failed", "cancelled"):
            return f"La misión #{mid} ya está en estado {mission.status}."
        await self._missions.set_status(mid, "cancelled")
        return await self._receipt(f"🗑️ Cancelada misión #{mid}: {truncate(mission.instruction)}")

    # ---- proactive code ----------------------------------------------------

    async def pedir_credenciales(self, nombres: str) -> str:
        if not self._allowed("pedir_credenciales"):
            return DENIED
        text = (nombres or "").strip()
        if not text or len(text) > 500:
            return "No pude prepararlo: indica qué credencial quieres cargar (1–500 caracteres)."
        if self._credentials is None:
            return "No pude prepararlo: la cola de credenciales no está disponible."
        await self._credentials.add(self._a.user_id, self._a.chat_id, text)
        return await self._receipt(f"🔑 Te preparo el link seguro para: {truncate(text)}")

    async def ver_skills(self) -> str:
        if not self._allowed("ver_skills"):
            return DENIED
        if self._skills is None:
            return "No pude verlos: el sistema de skills no está disponible."
        rows = self._skills.catalog()
        if not rows:
            return "No hay skills instalados."
        lines = []
        for s in rows:
            estado = "on" if s["enabled"] else "off"
            secretos = f" (necesita: {', '.join(s['required_secrets'])})" if s["required_secrets"] else ""
            desc = s["description"] or ""
            lines.append(f"• {s['name']} [{estado}]: {desc}{secretos}")
        return "\n".join(lines)

    async def activar_skill(self, nombre: str) -> str:
        return await self._toggle_skill(nombre, True, "Activé")

    async def desactivar_skill(self, nombre: str) -> str:
        return await self._toggle_skill(nombre, False, "Desactivé")

    async def _toggle_skill(self, nombre: str, enabled: bool, verb: str) -> str:
        if not self._allowed("activar_skill" if enabled else "desactivar_skill"):
            return DENIED
        if self._skills is None:
            return "No pude hacerlo: el sistema de skills no está disponible."
        query = (nombre or "").strip()
        names = [c["name"] for c in self._skills.catalog()]
        match = next((n for n in names if n == query), None) \
            or next((n for n in names if n.lower() == query.lower()), None)
        if match is None or not self._skills.set_enabled(match, enabled):
            listado = ", ".join(names) if names else "(ninguno)"
            name_to_show = query if match is None else match
            return f"No encontré un skill «{name_to_show}». Tienes: {listado}."
        extra = " Si le falta la credencial, te mando el link cuando lo uses." if enabled else ""
        return await self._receipt(f"🧩 {verb} el skill «{match}».{extra}")

    async def proponer_codigo(self, instruccion: str, carpeta: str | None = None) -> str:
        if not self._allowed("proponer_codigo"):
            return DENIED
        text = (instruccion or "").strip()
        if not text or len(text) > 2000:
            return "No pude prepararlo: la instrucción debe tener entre 1 y 2000 caracteres."
        target = (carpeta or "").strip() or None
        if target is not None and len(target) > 500:
            return "No pude prepararlo: la carpeta es demasiado larga."
        if self._coding is None:
            return "No pude prepararlo: la cola de código no está disponible."
        await self._coding.add(self._a.user_id, self._a.chat_id, text, target)
        return await self._receipt(f"🛠️ Preparando plan: {truncate(text)}")

    async def proponer_comando(self, comando: str) -> str:
        if not self._allowed("proponer_comando"):
            return DENIED
        text = (comando or "").strip()
        if not text or len(text) > 2000:
            return "No pude prepararlo: el comando debe tener entre 1 y 2000 caracteres."
        if self._commands is None:
            return "No pude prepararlo: la cola de comandos no está disponible."
        await self._commands.add(self._a.user_id, self._a.chat_id, text)
        return await self._receipt(f"⌨️ Preparando comando: {truncate(text)}")
