from datetime import datetime

QuietWindow = tuple[int, int]


def parse_window(spec: str) -> QuietWindow | None:
    """"22-7" -> (22, 7); "" -> None (no quiet hours)."""
    spec = (spec or "").strip()
    if not spec:
        return None
    start, sep, end = spec.partition("-")
    if not sep:
        raise ValueError(f"invalid quiet hours: {spec!r}")
    s, e = int(start), int(end)
    if not (0 <= s <= 23 and 0 <= e <= 23):
        raise ValueError(f"invalid quiet hours: {spec!r}")
    return (s, e)


def is_quiet(local: datetime, window: QuietWindow | None) -> bool:
    """``local`` must already be in the configured timezone."""
    if window is None:
        return False
    s, e = window
    if s == e:
        return False
    if s < e:
        return s <= local.hour < e
    return local.hour >= s or local.hour < e
