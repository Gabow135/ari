"""Logic behind Ari's own MCP tools. Every method acts for the Actor given by
Ari (never chosen by the model), checks the permission table, validates with the
domain rules, and records a code-generated receipt for each change."""
import logging
from dataclasses import dataclass

from ari.application.access.gate import normalize_code
from ari.application.schedule.agenda_format import created_receipt, item_line
from ari.domain.access.entities import APPROVED, PENDING
from ari.domain.schedule.actions import ActionError, next_cron_run, parse_action
from ari.domain.schedule.entities import ACTIVE, CANCELLED, PAUSED, REMINDER, RUNNING, TASK
from ari.domain.tools.ari_permissions import allowed_ari_tools

log = logging.getLogger("ari.tools")

DENIED = "No permitido en este contexto."
_KINDS = {"recordatorio": REMINDER, "tarea": TASK}


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
                 clock, gate=None, access=None):
        self._a, self._schedule, self._memory, self._log = actor, schedule, memory, turn_log
        self._tz, self._max, self._clock = tz, max_items, clock
        self._gate, self._access = gate, access

    def _allowed(self, tool: str) -> bool:
        if tool in allowed_ari_tools(self._a.is_owner, self._a.context):
            return True
        log.warning("denied %s for %s in context %s", tool, self._a.user_id, self._a.context)
        return False

    async def _receipt(self, text: str) -> str:
        await self._log.add_receipt(self._a.turn_id, text)
        return text

    # ---- agenda -----------------------------------------------------------

    async def agendar(self, tipo: str, texto: str, at: str | None = None,
                      cron: str | None = None) -> str:
        if not self._allowed("agendar"):
            return DENIED
        kind = _KINDS.get((tipo or "").strip().lower())
        if kind is None:
            return "No pude agendarlo: el tipo debe ser «recordatorio» o «tarea»."
        now = self._clock()
        try:
            action = parse_action({"type": kind, "text": texto, "at": at, "cron": cron},
                                  now, self._tz)
        except ActionError as exc:
            return f"No pude agendarlo: {exc}."
        if await self._schedule.count_active(self._a.user_id) >= self._max:
            return (f"No pude agendarlo: ya tienes {self._max} recordatorios o tareas "
                    "activos. Cancela alguno primero.")
        next_run = action.at or next_cron_run(action.cron, now, self._tz)
        item_id = await self._schedule.add(self._a.user_id, self._a.chat_id, action.kind,
                                           action.text, next_run, action.cron)
        return await self._receipt(created_receipt(item_id, action.kind, action.text,
                                                   next_run, action.cron, self._tz))

    async def listar_agenda(self) -> str:
        if not self._allowed("listar_agenda"):
            return DENIED
        items = await self._schedule.list_for_user(self._a.user_id)
        if not items:
            return "No tienes recordatorios ni tareas activos."
        return "\n".join(item_line(i, self._tz) for i in items)

    async def cancelar(self, id: int) -> str:
        if not self._allowed("cancelar"):
            return DENIED
        try:
            id_int = int(id)
        except (ValueError, TypeError):
            return f"No encontré el #{id} entre tus recordatorios."
        item = await self._schedule.get(id_int)
        if (item is None or item.user_id != self._a.user_id
                or item.status not in (ACTIVE, PAUSED, RUNNING)):
            return f"No encontré el #{id_int} entre tus recordatorios."
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
        await self._memory.upsert_fact(self._a.user_id, clave, valor)
        return await self._receipt(f"🧠 Guardé: {clave} = {valor}")

    async def olvidar_dato(self, clave: str) -> str:
        if not self._allowed("olvidar_dato"):
            return DENIED
        clave = (clave or "").strip()
        if not await self._memory.delete_fact(self._a.user_id, clave):
            return f"No tenía guardado «{clave}»."
        return await self._receipt(f"🧹 Olvidé: {clave}")

    async def ver_datos(self) -> str:
        if not self._allowed("ver_datos"):
            return DENIED
        facts = await self._memory.get_facts(self._a.user_id)
        if not facts:
            return "No tengo datos guardados de ti."
        return "\n".join(f"- {f.key}: {f.value}" for f in facts)

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
