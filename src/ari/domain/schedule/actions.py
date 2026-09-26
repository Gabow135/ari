"""Validation for creating a scheduled reminder or task (pure: no I/O). Used by
``AriTools.agendar`` (the real path, via Ari's MCP tools) and by anything else
that needs to validate an ``at``/``cron`` pair against the same rules."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo

from croniter import croniter

from ari.domain.schedule.entities import REMINDER, TASK

MAX_TEXT = 500
MAX_AHEAD = timedelta(days=366)
MIN_INTERVAL = timedelta(hours=1)
_DOW = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"]


class ActionError(ValueError):
    """Invalid action; ``str(e)`` is shown to the user."""


@dataclass(frozen=True)
class CreateAction:
    kind: str  # REMINDER | TASK
    text: str
    at: datetime | None  # UTC, one-shot
    cron: str | None  # recurring, local time


def next_cron_run(cron: str, after_utc: datetime, tz: tzinfo) -> datetime:
    nxt = croniter(cron, after_utc.astimezone(tz)).get_next(datetime)
    return nxt.astimezone(timezone.utc)


def _interval_ok(cron: str, now_utc: datetime, tz: tzinfo) -> bool:
    it = croniter(cron, now_utc.astimezone(tz))
    runs = [it.get_next(datetime) for _ in range(6)]
    return all(b - a >= MIN_INTERVAL for a, b in zip(runs, runs[1:]))


def parse_action(data: object, now_utc: datetime, tz: tzinfo) -> CreateAction:
    if not isinstance(data, dict):
        raise ActionError("formato inválido")
    kind = data.get("type")
    if kind not in (REMINDER, TASK):
        raise ActionError(f"tipo de acción desconocido: {kind!r}")
    text = str(data.get("text") or "").strip()
    if not text:
        raise ActionError("falta el texto")
    if len(text) > MAX_TEXT:
        raise ActionError("el texto es demasiado largo")
    at_raw, cron = data.get("at"), data.get("cron")
    if at_raw and cron:
        raise ActionError("indica una fecha («at») o una repetición («cron»), no ambas")
    if not at_raw and not cron:
        raise ActionError("falta la fecha («at») o la repetición («cron»)")
    if cron:
        cron = str(cron).strip()
        if len(cron.split()) != 5 or not croniter.is_valid(cron):
            raise ActionError(f"la repetición «{cron}» no es válida")
        if not _interval_ok(cron, now_utc, tz):
            raise ActionError("la repetición mínima es cada 1 hora")
        return CreateAction(kind, text, None, cron)
    try:
        at = datetime.fromisoformat(str(at_raw))
    except ValueError:
        raise ActionError(f"la fecha «{at_raw}» no es válida") from None
    if at.tzinfo is None:
        at = at.replace(tzinfo=tz)  # no offset given: it's local time
    at = at.astimezone(timezone.utc)
    if at <= now_utc:
        raise ActionError("la fecha ya pasó")
    if at - now_utc > MAX_AHEAD:
        raise ActionError("la fecha está a más de un año")
    return CreateAction(kind, text, at, None)


def describe_cron(cron: str) -> str:
    parts = cron.split()
    if len(parts) == 5:
        minute, hour, dom, month, dow = parts
        if minute.isdigit() and hour.isdigit() and dom == "*" and month == "*":
            at = f"{int(hour):02d}:{int(minute):02d}"
            if dow == "*":
                return f"cada día {at}"
            days = dow.split(",")
            if all(d.isdigit() and 0 <= int(d) <= 7 for d in days):
                return f"cada {', '.join(_DOW[int(d) % 7] for d in days)} {at}"
    return f"según cron {cron}"
