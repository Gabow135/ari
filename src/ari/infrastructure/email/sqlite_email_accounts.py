import asyncio
from datetime import datetime, timezone

import aiosqlite

from ari.domain.crypto.secret_cipher import SecretCipher
from ari.domain.email.entities import EmailAccount, EmailAccountSummary, mask_address

_COLS = ("imap_host, imap_port, imap_secure, smtp_host, smtp_port, smtp_secure, "
         "email_user, pass_enc")


class SqliteEmailAccounts:
    """EmailAccountsPort over user_email_accounts. The cipher is required only to
    read/write the password; masked summaries and removal work without it (the
    MCP server process has no vault key)."""

    def __init__(self, conn: aiosqlite.Connection, cipher: SecretCipher | None):
        self._conn = conn
        self._cipher = cipher
        self._lock = asyncio.Lock()

    def _require_cipher(self) -> SecretCipher:
        if self._cipher is None:
            raise RuntimeError("email cipher unavailable (ARI_VAULT_KEY missing)")
        return self._cipher

    async def add(self, account: EmailAccount) -> None:
        pass_enc = self._require_cipher().encrypt(account.password)
        async with self._lock:
            await self._conn.execute(
                "INSERT INTO user_email_accounts (user_id, label, imap_host, imap_port, "
                "imap_secure, smtp_host, smtp_port, smtp_secure, email_user, pass_enc, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, label) DO UPDATE SET "
                "imap_host=excluded.imap_host, imap_port=excluded.imap_port, "
                "imap_secure=excluded.imap_secure, smtp_host=excluded.smtp_host, "
                "smtp_port=excluded.smtp_port, smtp_secure=excluded.smtp_secure, "
                "email_user=excluded.email_user, pass_enc=excluded.pass_enc",
                (account.user_id, account.label, account.imap_host, account.imap_port,
                 int(account.imap_secure), account.smtp_host, account.smtp_port,
                 int(account.smtp_secure), account.email_user, pass_enc,
                 datetime.now(timezone.utc).isoformat(timespec="seconds")))
            await self._conn.commit()

    async def list_for_user(self, user_id: str) -> list[EmailAccount]:
        cipher = self._require_cipher()
        rows = await self._conn.execute_fetchall(
            f"SELECT label, {_COLS} FROM user_email_accounts WHERE user_id = ? "
            "ORDER BY label", (user_id,))
        return [EmailAccount(
            user_id, r["label"], r["imap_host"], r["imap_port"], bool(r["imap_secure"]),
            r["smtp_host"], r["smtp_port"], bool(r["smtp_secure"]), r["email_user"],
            cipher.decrypt(r["pass_enc"])) for r in rows]

    async def summaries_for(self, user_id: str) -> list[EmailAccountSummary]:
        rows = await self._conn.execute_fetchall(
            "SELECT label, email_user FROM user_email_accounts WHERE user_id = ? "
            "ORDER BY label", (user_id,))
        return [EmailAccountSummary(r["label"], mask_address(r["email_user"])) for r in rows]

    async def remove(self, user_id: str, label: str) -> bool:
        async with self._lock:
            cur = await self._conn.execute(
                "DELETE FROM user_email_accounts WHERE user_id = ? AND label = ?",
                (user_id, label))
            await self._conn.commit()
            return cur.rowcount > 0

    async def delete_for_user(self, user_id: str) -> int:
        async with self._lock:
            cur = await self._conn.execute(
                "DELETE FROM user_email_accounts WHERE user_id = ?", (user_id,))
            await self._conn.commit()
            return cur.rowcount
