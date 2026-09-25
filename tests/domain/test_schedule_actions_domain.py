from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.domain.schedule.actions import (
    ActionError,
    CancelAction,
    CreateAction,
    describe_cron,
    next_cron_run,
    parse_action,
)
from ari.domain.schedule.entities import REMINDER, TASK

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)  # 15:00 local, Friday


def test_one_shot_reminder_with_offset():
    act = parse_action({"type": "reminder", "at": "2026-09-26T09:00:00-05:00",
                        "text": "llamar a Juan"}, NOW, TZ)
    assert act == CreateAction(REMINDER, "llamar a Juan",
                               datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc), None)


def test_naive_at_is_local_time():
    act = parse_action({"type": "reminder", "at": "2026-09-26T09:00", "text": "x"}, NOW, TZ)
    assert act.at == datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc)


def test_recurring_task():
    act = parse_action({"type": "task", "cron": "0 8 * * 1", "text": "resumen"}, NOW, TZ)
    assert act == CreateAction(TASK, "resumen", None, "0 8 * * 1")


def test_cancel():
    assert parse_action({"type": "cancel", "id": "12"}, NOW, TZ) == CancelAction(12)


@pytest.mark.parametrize("data,reason", [
    ({"type": "reminder", "at": "2026-09-25T10:00:00-05:00", "text": "x"}, "ya pasó"),
    ({"type": "reminder", "at": "2028-01-01T10:00:00-05:00", "text": "x"}, "más de un año"),
    ({"type": "reminder", "at": "mañana", "text": "x"}, "no es válida"),
    ({"type": "reminder", "text": "x"}, "fecha"),
    ({"type": "reminder", "at": "2026-09-26T09:00", "cron": "0 8 * * *", "text": "x"}, "no ambas"),
    ({"type": "task", "cron": "*/5 * * * *", "text": "x"}, "1 hora"),
    ({"type": "task", "cron": "0 8 * *", "text": "x"}, "no es válida"),
    ({"type": "reminder", "at": "2026-09-26T09:00", "text": ""}, "texto"),
    ({"type": "reminder", "at": "2026-09-26T09:00", "text": "x" * 501}, "largo"),
    ({"type": "explode"}, "desconocido"),
    ({"type": "cancel"}, "número"),
    ("not a dict", "formato"),
])
def test_invalid_actions_explain_why(data, reason):
    with pytest.raises(ActionError, match=reason):
        parse_action(data, NOW, TZ)


def test_next_cron_run_is_computed_in_local_time():
    # next Monday 08:00 local = 13:00 UTC
    assert next_cron_run("0 8 * * 1", NOW, TZ) == datetime(2026, 9, 28, 13, 0,
                                                           tzinfo=timezone.utc)


def test_next_cron_run_skips_missed_occurrences():
    long_after = NOW + timedelta(days=20)
    assert next_cron_run("0 8 * * *", long_after, TZ) > long_after


@pytest.mark.parametrize("cron,text", [
    ("0 8 * * *", "cada día 08:00"),
    ("30 7 * * 1", "cada lunes 07:30"),
    ("0 9 * * 1,3,5", "cada lunes, miércoles, viernes 09:00"),
    ("0 9 1 * *", "según cron 0 9 1 * *"),
])
def test_describe_cron(cron, text):
    assert describe_cron(cron) == text
