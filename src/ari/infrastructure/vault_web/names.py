"""The allowlist of vault secret names the web maintainer may write: every ${VAR}
referenced in servers.json except ARI_FS_ROOT (a path, not a vault secret)."""
import re

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_EXCLUDE = {"ARI_FS_ROOT"}

# Human-readable metadata for credentials shown in the web form: name → (label, description).
KNOWN_LABELS: dict[str, tuple[str, str]] = {
    "HUGGINGFACE_TOKEN": (
        "HuggingFace Token",
        "Token de acceso a HuggingFace para pyannote.audio",
    ),
    "ARI_GMAIL_USER": ("Gmail: dirección", "Tu dirección de Gmail (correo completo)"),
    "ARI_GMAIL_PASS": ("Gmail: App Password", "App Password de Gmail (no la contraseña normal)"),
    "ARI_HOTMAIL_USER": ("Hotmail/Outlook: dirección", "Tu dirección de Hotmail/Outlook (correo completo)"),
    "ARI_HOTMAIL_PASS": ("Hotmail/Outlook: App Password", "App Password de Outlook (no la contraseña normal)"),
    "ARI_EMAIL_USER": ("Corporativo: dirección", "Dirección del correo corporativo/cPanel"),
    "ARI_EMAIL_PASS": ("Corporativo: contraseña", "Contraseña del correo corporativo/cPanel"),
    "ARI_IMAP_HOST": ("Corporativo: host IMAP", "Servidor IMAP del correo corporativo (ej: mail.tudominio.com)"),
    "ARI_SMTP_HOST": ("Corporativo: host SMTP", "Servidor SMTP del correo corporativo (ej: mail.tudominio.com)"),
}


def configurable_secret_names(servers_json_path: str) -> list[str]:
    try:
        with open(servers_json_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    return sorted(set(_VAR.findall(text)) - _EXCLUDE)
