"""The allowlist of vault secret names the web maintainer may write: every ${VAR}
referenced in servers.json except ARI_FS_ROOT (a path, not a vault secret)."""
import re

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_EXCLUDE = {"ARI_FS_ROOT"}


def configurable_secret_names(servers_json_path: str) -> list[str]:
    try:
        with open(servers_json_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    return sorted(set(_VAR.findall(text)) - _EXCLUDE)
