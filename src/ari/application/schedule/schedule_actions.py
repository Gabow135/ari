import re
from datetime import datetime, timezone

from ari.application.schedule.agenda_format import item_line
from ari.domain.schedule.timefmt import fmt_long

_BLOCK = re.compile(r"<ari-action>(.*?)</ari-action>", re.S | re.I)
_DANGLING = re.compile(r"<ari-action>.*\Z", re.S | re.I)

_TOOLS_HINT = """## Tu agenda y tus datos
Para recordatorios, tareas y datos del usuario usa las herramientas del servidor «ari»:
agendar, listar_agenda, cancelar, recordar_dato, olvidar_dato y ver_datos (si hablas con
tu creador, también aprobar_acceso, revocar_acceso, ver_accesos y enviar_mensaje).
Calcula «at» a partir de la fecha y hora actual de arriba. El sistema agrega al final la
confirmación exacta de lo que hiciste: no inventes números ni horas."""


def extract_actions(reply: str) -> tuple[str, list[str]]:
    """Split a reply into (text the user sees, raw JSON of each legacy action block).
    Blocks are no longer honored; this only keeps stray ones out of sight."""
    blocks = [b.strip() for b in _BLOCK.findall(reply)]
    clean = _DANGLING.sub("", _BLOCK.sub("", reply))  # an unclosed block never leaks
    return clean.strip(), blocks


class ScheduleActions:
    """Prompt context about time and agenda tools, and the /recordatorios listing."""

    def __init__(self, store, tz, max_items: int,
                 clock=lambda: datetime.now(timezone.utc)):
        self._store, self._tz, self._max, self._clock = store, tz, max_items, clock

    async def context(self, user_id: str) -> str:
        return (f"## Fecha y hora actual\n{fmt_long(self._clock(), self._tz)} "
                f"({self._tz.key})\n\n{_TOOLS_HINT}")

    async def list_text(self, user_id: str) -> str:
        items = await self._store.list_for_user(user_id)
        if not items:
            return "No tienes recordatorios ni tareas activos."
        return "Tus recordatorios y tareas:\n" + "\n".join(item_line(i, self._tz) for i in items)
