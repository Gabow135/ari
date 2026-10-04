import json

from ari.domain.email.entities import EmailAccount
from ari.infrastructure.email.server_spec import valid_label

EMAIL_BLOB_PREFIX = "ari-mail:v1:"
_FIELDS = ("imap_host", "imap_port", "smtp_host", "smtp_port", "email_user", "password")


def is_email_blob(text: str) -> bool:
    return text.strip().startswith(EMAIL_BLOB_PREFIX)


class ConnectEmailAccount:
    def __init__(self, sealed_box, accounts):
        self._box, self._accounts = sealed_box, accounts

    async def __call__(self, user_id: str, blob: str) -> str:
        body = blob.strip()[len(EMAIL_BLOB_PREFIX):]
        plain = self._box.open(body)  # raises ValueError on a bad blob
        try:
            data = json.loads(plain)
        except json.JSONDecodeError as exc:
            raise ValueError("el código no tenía un formato válido") from exc
        label = str(data.get("label", "")).strip().lower()
        if not valid_label(label):
            raise ValueError("la etiqueta debe ser corta, sin espacios ni símbolos raros")
        if any(not data.get(f) for f in _FIELDS):
            raise ValueError("faltan datos de la casilla (host, puerto, usuario o contraseña)")
        account = EmailAccount(
            user_id, label, data["imap_host"], int(data["imap_port"]),
            bool(data.get("imap_secure", True)), data["smtp_host"], int(data["smtp_port"]),
            bool(data.get("smtp_secure", True)), data["email_user"], data["password"])
        await self._accounts.add(account)
        return label
