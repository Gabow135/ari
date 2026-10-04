import base64

import pytest
from nacl.public import PublicKey, SealedBox

from ari.infrastructure.email.sealed_box import AriSealedBox


class FakeVault:
    def __init__(self, writable=True):
        self._d, self._writable = {}, writable

    def get(self, name):
        return self._d.get(name)

    def set(self, name, value):
        if not self._writable:
            raise RuntimeError("no key")
        self._d[name] = value


def test_round_trip_and_key_is_persisted():
    box = AriSealedBox(FakeVault())
    pub = base64.b64decode(box.public_key_b64())
    sealed = SealedBox(PublicKey(pub)).encrypt(b"hello")
    assert box.open(base64.b64encode(sealed).decode()) == b"hello"
    # second instance on the SAME vault reuses the stored key
    box2 = AriSealedBox(box._vault)
    assert box2.public_key_b64() == box.public_key_b64()


def test_bad_blob_raises_value_error():
    box = AriSealedBox(FakeVault())
    box.public_key_b64()
    with pytest.raises(ValueError):
        box.open("not-base64-!!")


def test_corrupt_ciphertext_raises_value_error():
    box = AriSealedBox(FakeVault())
    box.public_key_b64()  # ensure key is persisted
    with pytest.raises(ValueError):
        box.open(base64.b64encode(b"this is garbage, not a real sealed blob").decode())


def test_unavailable_when_vault_cannot_store():
    box = AriSealedBox(FakeVault(writable=False))
    assert box.available() is False
