from datetime import datetime, tzinfo

_DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
_DAYS_LONG = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def fmt_short(dt: datetime, tz: tzinfo) -> str:
    local = dt.astimezone(tz)
    return f"{_DAYS[local.weekday()]} {local:%d/%m %H:%M}"


def fmt_long(dt: datetime, tz: tzinfo) -> str:
    local = dt.astimezone(tz)
    return f"{_DAYS_LONG[local.weekday()]} {local:%d/%m/%Y %H:%M}"
