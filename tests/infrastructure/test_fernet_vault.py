import os

import pytest
from cryptography.fernet import Fernet

from ari.infrastructure.vault.fernet_vault import FernetVault

KEY = Fernet.generate_key().decode()


def _vault(tmp_path, key=KEY):
    return FernetVault(str(tmp_path / ".ari" / "vault.enc"), key)


def test_set_get_roundtrip(tmp_path):
    v = _vault(tmp_path)
    v.set("GID", "gid-1")
    assert v.get("GID") == "gid-1"


def test_names_returns_names_only_sorted(tmp_path):
    v = _vault(tmp_path)
    v.set("B", "2")
    v.set("A", "1")
    assert v.names() == ["A", "B"]


def test_delete_removes_entry(tmp_path):
    v = _vault(tmp_path)
    v.set("GID", "gid-1")
    v.delete("GID")
    assert v.get("GID") is None


def test_missing_key_disables_vault(tmp_path):
    v = _vault(tmp_path, key="")
    assert v.get("GID") is None
    assert v.names() == []
    with pytest.raises(RuntimeError):
        v.set("GID", "x")


def test_missing_file_returns_none(tmp_path):
    v = _vault(tmp_path)  # nothing written yet
    assert v.get("GID") is None


def test_wrong_key_falls_back_without_raising(tmp_path, caplog):
    _vault(tmp_path).set("GID", "gid-1")
    other = FernetVault(str(tmp_path / ".ari" / "vault.enc"),
                        Fernet.generate_key().decode())
    assert other.get("GID") is None  # cannot decrypt, but no raise


def test_corrupt_file_falls_back_without_raising(tmp_path):
    v = _vault(tmp_path)
    v.set("GID", "gid-1")
    path = tmp_path / ".ari" / "vault.enc"
    path.write_bytes(b"not a fernet token")
    assert v.get("GID") is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX perms")
def test_file_is_0600_and_dir_0700(tmp_path):
    v = _vault(tmp_path)
    v.set("GID", "gid-1")
    path = tmp_path / ".ari" / "vault.enc"
    assert (os.stat(path).st_mode & 0o777) == 0o600
    assert (os.stat(tmp_path / ".ari").st_mode & 0o777) == 0o700


def test_no_leftover_temp_file(tmp_path):
    v = _vault(tmp_path)
    v.set("GID", "gid-1")
    files = os.listdir(tmp_path / ".ari")
    assert files == ["vault.enc"]
