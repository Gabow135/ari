import unicodedata

_AFFIRMATIVE = {"dale", "si", "sí", "ok", "okay", "yes", "dale total"}
_NEGATIVE = {"no", "cancelar", "cancel", "nope"}

# Exact command tokens (strip @botname suffix before comparing).
_COMMANDS = {"/code", "/fase2"}


def _parse_token(raw: str) -> str:
    """Return the bare command, stripping any @mention suffix."""
    at = raw.find("@")
    return raw[:at] if at != -1 else raw


def parse_coding_command(text: str) -> tuple[str, str | None] | None:
    stripped = text.strip()
    parts = stripped.split(None, 1)  # split at most into [token, rest]
    if not parts:
        return None
    token = _parse_token(parts[0])
    if token not in _COMMANDS:
        return None
    rest = parts[1].strip() if len(parts) > 1 else ""
    if not rest:
        return None
    target = None
    if rest.startswith("dir:"):
        head, _, tail = rest.partition(" ")
        target = head[len("dir:"):] or None
        rest = tail.strip()
    if not rest:
        return None
    return rest, target


def is_affirmative(text: str) -> bool:
    return text.strip().casefold() in _AFFIRMATIVE


def is_negative(text: str) -> bool:
    return text.strip().casefold() in _NEGATIVE


def is_dale(text: str) -> bool:
    """Exact confirmation for a proposed (proponer_codigo) plan: only "dale",
    case-insensitive/trimmed and stripped of trailing punctuation or emoji —
    not "dale total" nor any other affirmative."""
    core = text.strip()
    while core and unicodedata.category(core[-1])[0] in ("P", "S"):
        core = core[:-1]
    return core.strip().casefold() == "dale"
