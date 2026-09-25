import json
import logging
import re
from datetime import datetime, timezone

from ari.domain.schedule.actions import (
    ActionError, CancelAction, describe_cron, next_cron_run, parse_action)
from ari.domain.schedule.entities import ACTIVE, CANCELLED, PAUSED, REMINDER, RUNNING, ScheduleItem
from ari.domain.schedule.timefmt import fmt_long, fmt_short

log = logging.getLogger("ari.schedule")

_BLOCK = re.compile(r"<ari-action>(.*?)</ari-action>", re.S | re.I)
_DANGLING = re.compile(r"<ari-action>.*\Z", re.S | re.I)

_FORMAT = """## Cómo agendar
Si el usuario te pide recordarle algo, o hacer algo más tarde o de forma periódica,
agrega AL FINAL de tu respuesta un bloque por acción. El usuario no ve los bloques;
el sistema agrega la confirmación exacta, así que no inventes números ni horas.
<ari-action>{"type":"reminder","at":"2026-09-26T09:00:00-05:00","text":"llamar a Juan"}</ari-action>
<ari-action>{"type":"task","cron":"0 8 * * 1","text":"resúmeme mis pendientes"}</ari-action>
<ari-action>{"type":"cancel","id":12}</ari-action>
- "reminder": a esa hora se envía el texto tal cual. "task": a esa hora TÚ ejecutas
  la instrucción y envías el resultado.
- Usa "at" (fecha y hora local ISO) para una vez, o "cron" (5 campos, hora local)
  para repetir. Frecuencia mínima: cada 1 hora.
- Calcula "at" a partir de la fecha y hora actual de arriba, no de las fechas del
  ejemplo (esas son solo ilustrativas).
- Para cancelar usa el # de la lista de arriba."""


def extract_actions(reply: str) -> tuple[str, list[str]]:
    """Split a reply into (text the user sees, raw JSON of each action block)."""
    blocks = [b.strip() for b in _BLOCK.findall(reply)]
    clean = _DANGLING.sub("", _BLOCK.sub("", reply))  # an unclosed block never leaks
    return clean.strip(), blocks


class ScheduleActions:
    def __init__(self, store, tz, max_items: int,
                 clock=lambda: datetime.now(timezone.utc)):
        self._store, self._tz, self._max, self._clock = store, tz, max_items, clock

    def _when(self, item: ScheduleItem) -> str:
        if item.cron:
            return f"{describe_cron(item.cron)} (próxima {fmt_short(item.next_run_at, self._tz)})"
        return fmt_short(item.next_run_at, self._tz)

    def _line(self, item: ScheduleItem) -> str:
        paused = " · ⏸️ pausada" if item.status == PAUSED else ""
        return f"#{item.id} · {self._when(item)} · {item.text}{paused}"

    async def context(self, user_id: str) -> str:
        items = await self._store.list_for_user(user_id)
        listing = "\n".join(f"- {self._line(i)}" for i in items) or "Ninguno."
        return (f"## Fecha y hora actual\n{fmt_long(self._clock(), self._tz)} "
                f"({self._tz.key})\n\n"
                f"## Recordatorios y tareas del usuario\n{listing}\n\n{_FORMAT}")

    async def list_text(self, user_id: str) -> str:
        items = await self._store.list_for_user(user_id)
        if not items:
            return "No tienes recordatorios ni tareas activos."
        return "Tus recordatorios y tareas:\n" + "\n".join(self._line(i) for i in items)

    async def apply(self, user_id: str, chat_id: str, reply: str, allow: bool = True) -> str:
        clean, blocks = extract_actions(reply)
        if not allow or not blocks:
            return clean
        notes = [await self._apply_one(user_id, chat_id, raw) for raw in blocks]
        return "\n\n".join(p for p in (clean, "\n".join(notes)) if p)

    async def _apply_one(self, user_id: str, chat_id: str, raw: str) -> str:
        now = self._clock()
        try:
            action = parse_action(json.loads(raw), now, self._tz)
        except (ValueError, ActionError) as exc:  # JSONDecodeError is a ValueError
            reason = str(exc) if isinstance(exc, ActionError) else "el formato no es válido"
            log.info("rejected action from %s: %s (%r)", user_id, reason, raw[:200])
            return f"⚠️ No pude agendarlo: {reason}."
        if isinstance(action, CancelAction):
            item = await self._store.get(action.id)
            if (item is None or item.user_id != user_id
                    or item.status not in (ACTIVE, PAUSED, RUNNING)):
                return f"⚠️ No encontré el #{action.id} entre tus recordatorios."
            await self._store.set_status(item.id, CANCELLED)
            return f"🗑️ Cancelado #{item.id}: {item.text}"
        if await self._store.count_active(user_id) >= self._max:
            return (f"⚠️ No pude agendarlo: ya tienes {self._max} recordatorios o tareas "
                    "activos. Cancela alguno primero.")
        next_run = action.at or next_cron_run(action.cron, now, self._tz)
        item_id = await self._store.add(user_id, chat_id, action.kind, action.text,
                                        next_run, action.cron)
        label = "Recordatorio" if action.kind == REMINDER else "Tarea"
        when = fmt_short(next_run, self._tz)
        if action.cron:
            return (f"🔁 {label} #{item_id} ({describe_cron(action.cron)}): "
                    f"{action.text} — próxima: {when}")
        return f"✅ {label} #{item_id}: {action.text} — {when}"
