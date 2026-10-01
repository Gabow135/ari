import re

_VAR = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
_NEVER = {"ARI_FS_ROOT"}
_MAX_NAMES = 20
_MAX_LEN = 64


def normalize_secret_names(raw: str) -> list[str]:
    """Extract valid vault secret names from the free text Ari passed. Splits on
    commas/whitespace, uppercases, keeps UPPER_SNAKE tokens with at least one
    underscore, drops the never-writable path, dedups (stable order), and caps."""
    out: list[str] = []
    seen: set[str] = set()
    for tok in re.split(r"[,\s]+", raw or ""):
        name = tok.strip().upper()
        if not name or len(name) > _MAX_LEN or name in _NEVER or name in seen:
            continue
        if _VAR.match(name):
            seen.add(name)
            out.append(name)
            if len(out) >= _MAX_NAMES:
                break
    return out
