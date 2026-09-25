_AFFIRMATIVE = {"dale", "si", "sí", "ok", "okay", "yes", "dale total"}
_NEGATIVE = {"no", "cancelar", "cancel", "nope"}


def parse_coding_command(text: str) -> tuple[str, str | None] | None:
    stripped = text.strip()
    for prefix in ("/code", "/fase2"):
        if stripped.startswith(prefix):
            rest = stripped[len(prefix):].strip()
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
    return None


def is_affirmative(text: str) -> bool:
    return text.strip().casefold() in _AFFIRMATIVE


def is_negative(text: str) -> bool:
    return text.strip().casefold() in _NEGATIVE
