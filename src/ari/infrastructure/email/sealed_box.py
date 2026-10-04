import base64
import binascii

from nacl.public import PrivateKey, SealedBox

_KEY_NAME = "ARI_SEALEDBOX_SK"


class AriSealedBox:
    """Ari's long-lived X25519 key pair. The secret key is stored in the vault;
    the public key is handed to the enroll HTML. Opens sealed blobs clients make."""

    def __init__(self, vault, key_name: str = _KEY_NAME):
        self._vault = vault
        self._key_name = key_name

    def _secret_key(self) -> PrivateKey:
        stored = self._vault.get(self._key_name)
        if stored:
            return PrivateKey(base64.b64decode(stored))
        sk = PrivateKey.generate()
        self._vault.set(self._key_name, base64.b64encode(bytes(sk)).decode())
        return sk

    def available(self) -> bool:
        try:
            self._secret_key()
            return True
        except Exception:
            return False

    def public_key_b64(self) -> str:
        return base64.b64encode(bytes(self._secret_key().public_key)).decode()

    def open(self, sealed_b64: str) -> bytes:
        try:
            raw = base64.b64decode(sealed_b64, validate=True)
            return SealedBox(self._secret_key()).decrypt(raw)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("blob ilegible") from exc
        except Exception as exc:  # nacl.exceptions.CryptoError and friends
            raise ValueError("no pude descifrar el blob") from exc
