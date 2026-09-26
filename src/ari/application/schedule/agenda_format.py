from datetime import datetime

from ari.domain.schedule.actions import describe_cron
from ari.domain.schedule.entities import PAUSED, REMINDER, ScheduleItem
from ari.domain.schedule.timefmt import fmt_short


def _when(next_run: datetime, cron: str | None, tz) -> str:
    if cron:
        return f"{describe_cron(cron)} (próxima {fmt_short(next_run, tz)})"
    return fmt_short(next_run, tz)


def item_line(item: ScheduleItem, tz) -> str:
    paused = " · ⏸️ pausada" if item.status == PAUSED else ""
    return f"#{item.id} · {_when(item.next_run_at, item.cron, tz)} · {item.text}{paused}"


def created_receipt(item_id: int, kind: str, text: str, next_run: datetime,
                    cron: str | None, tz) -> str:
    label = "Recordatorio" if kind == REMINDER else "Tarea"
    when = fmt_short(next_run, tz)
    if cron:
        return f"🔁 {label} #{item_id} ({describe_cron(cron)}): {text} — próxima: {when}"
    return f"✅ {label} #{item_id}: {text} — {when}"
