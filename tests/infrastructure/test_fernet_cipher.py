import pytest
from cryptography.fernet import Fernet
from ari.infrastructure.crypto.fernet_cipher import FernetCipher


def test_encrypt_decrypt_round_trip():
    cipher = FernetCipher(Fernet.generate_key().decode())
    token = cipher.encrypt("hunter2")
    assert token != "hunter2"
    assert cipher.decrypt(token) == "hunter2"


def test_empty_key_raises():
    with pytest.raises(RuntimeError):
        FernetCipher("")
