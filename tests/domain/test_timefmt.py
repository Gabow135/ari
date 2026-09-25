from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ari.domain.schedule.timefmt import fmt_long, fmt_short

TZ = ZoneInfo("America/Guayaquil")


def test_fmt_short_converts_utc_to_local():
    # 14:00 UTC = 09:00 in Guayaquil (UTC-5); 2026-09-26 is a Saturday
    assert fmt_short(datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc), TZ) == "sáb 26/09 09:00"


def test_fmt_long():
    assert fmt_long(datetime(2026, 9, 25, 20, 40, tzinfo=timezone.utc), TZ) == \
        "viernes 25/09/2026 15:40"
