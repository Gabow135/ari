"""Hard denylist of secret paths that the LAN file server must never serve."""
import os


def default_denied_roots(vault_path: str, claude_config_dir: str,
                         cert_key_path: str | None = None) -> list[str]:
    home = os.path.expanduser("~")
    raw = [
        os.path.expanduser(vault_path),
        os.path.abspath(claude_config_dir),
        os.path.join(home, ".ssh"),
        os.path.join(home, ".aws"),
        os.path.join(home, ".gnupg"),
        os.path.join(home, "Library", "Keychains"),
    ]
    if cert_key_path:
        raw.append(os.path.expanduser(cert_key_path))
    return [os.path.realpath(r) for r in raw]


def is_denied(path: str, denied_roots: list[str]) -> bool:
    real = os.path.realpath(os.path.expanduser(path))
    low = real.lower()
    if os.path.basename(low) == ".env":
        return True
    for root in denied_roots:
        rlow = root.lower()
        if low == rlow or low.startswith(rlow + os.sep):
            return True
    return False
