"""Encrypted-at-rest secret store: a single Fernet-encrypted JSON blob on disk.
The key comes from ARI_VAULT_KEY; without it the vault is transparently disabled
so the registry falls back to env/.env."""
import json
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

from ari.domain.vault.secret_vault import SecretVault

log = logging.getLogger("ari.vault")


class FernetVault(SecretVault):
    def __init__(self, path: str, key: str):
        self._path = os.path.expanduser(path)
        self._fernet = Fernet(key.encode()) if key else None

    @property
    def path(self) -> str:
        return self._path

    def _load(self) -> dict:
        if self._fernet is None or not os.path.exists(self._path):
            return {}
        try:
            with open(self._path, "rb") as f:
                return json.loads(self._fernet.decrypt(f.read()).decode())
        except (InvalidToken, ValueError, OSError) as exc:
            log.error("vault unreadable (%s); falling back to env/.env", exc)
            return {}

    def _save(self, data: dict) -> None:
        if self._fernet is None:
            raise RuntimeError("ARI_VAULT_KEY no está configurada")
        parent = os.path.dirname(self._path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
            os.chmod(parent, 0o700)
        token = self._fernet.encrypt(json.dumps(data).encode())
        tmp = self._path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(token)
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def get(self, name: str) -> str | None:
        return self._load().get(name)

    def set(self, name: str, value: str) -> None:
        data = self._load()
        data[name] = value
        self._save(data)

    def delete(self, name: str) -> None:
        data = self._load()
        data.pop(name, None)
        self._save(data)

    def names(self) -> list[str]:
        return sorted(self._load())
