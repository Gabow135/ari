from cryptography.fernet import Fernet

from ari.domain.crypto.secret_cipher import SecretCipher


class FernetCipher(SecretCipher):
    """Encrypts/decrypts a single string with the same ARI_VAULT_KEY the vault uses."""

    def __init__(self, key: str):
        if not key:
            raise RuntimeError("ARI_VAULT_KEY no está configurada")
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()
