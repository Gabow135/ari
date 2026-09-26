from cryptography.fernet import Fernet

import ari.vault as vault_cli
from ari.infrastructure.vault.fernet_vault import FernetVault


def test_init_prints_a_valid_fernet_key(capsys):
    vault_cli.main(["init"])
    printed = capsys.readouterr().out.strip()
    Fernet(printed.encode())  # raises if not a valid key


def test_set_reads_value_via_getpass_not_argv(tmp_path, monkeypatch, capsys):
    v = FernetVault(str(tmp_path / "vault.enc"), Fernet.generate_key().decode())
    monkeypatch.setattr(vault_cli, "_vault", lambda: v)
    monkeypatch.setattr(vault_cli.getpass, "getpass", lambda prompt="": "s3cret")
    vault_cli.main(["set", "DBPASS"])
    assert v.get("DBPASS") == "s3cret"


def test_list_prints_names_only(tmp_path, monkeypatch, capsys):
    v = FernetVault(str(tmp_path / "vault.enc"), Fernet.generate_key().decode())
    v.set("A", "1")
    monkeypatch.setattr(vault_cli, "_vault", lambda: v)
    vault_cli.main(["list"])
    assert capsys.readouterr().out.strip() == "A"


def test_delete_removes(tmp_path, monkeypatch):
    v = FernetVault(str(tmp_path / "vault.enc"), Fernet.generate_key().decode())
    v.set("A", "1")
    monkeypatch.setattr(vault_cli, "_vault", lambda: v)
    vault_cli.main(["delete", "A"])
    assert v.get("A") is None
