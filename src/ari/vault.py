"""Human-only entry point to load secrets into the vault: `python3 -m ari.vault`.
Values are read with getpass so they never appear on argv or in shell history."""
import argparse
import getpass

from cryptography.fernet import Fernet

from ari.config.settings import Settings
from ari.infrastructure.vault.fernet_vault import FernetVault


def _vault() -> FernetVault:
    s = Settings()
    return FernetVault(s.vault_path, s.vault_key)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ari.vault")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="print a fresh ARI_VAULT_KEY to paste into .env")
    p_set = sub.add_parser("set", help="store one secret (value read hidden)")
    p_set.add_argument("name")
    sub.add_parser("list", help="print stored names only")
    p_del = sub.add_parser("delete", help="remove one secret")
    p_del.add_argument("name")
    args = parser.parse_args(argv)

    if args.cmd == "init":
        print(Fernet.generate_key().decode())
        return

    vault = _vault()
    if args.cmd == "set":
        value = getpass.getpass(f"Valor de {args.name}: ")
        vault.set(args.name, value)
        print(f"Guardado {args.name}.")
    elif args.cmd == "list":
        for name in vault.names():
            print(name)
    elif args.cmd == "delete":
        vault.delete(args.name)
        print(f"Borrado {args.name}.")


if __name__ == "__main__":
    main()
