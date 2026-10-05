import aiosqlite
import sqlite_vec

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, role TEXT NOT NULL,
  content TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id);

CREATE TABLE IF NOT EXISTS recalls (
  id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, content TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_recalls_user ON recalls(user_id);

CREATE TABLE IF NOT EXISTS facts (
  user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
  updated_at TEXT NOT NULL, PRIMARY KEY (user_id, key));

CREATE TABLE IF NOT EXISTS summaries (
  user_id TEXT PRIMARY KEY, content TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS access (
  user_id TEXT PRIMARY KEY, username TEXT, code TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS grants (
  grantor_id TEXT NOT NULL, grantee_id TEXT NOT NULL, capability TEXT NOT NULL,
  level TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (grantor_id, grantee_id, capability));
CREATE INDEX IF NOT EXISTS idx_grants_grantee ON grants(grantee_id);

CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
  kind TEXT NOT NULL, text TEXT NOT NULL, next_run_at TEXT NOT NULL, cron TEXT,
  status TEXT NOT NULL, failures INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, last_run_at TEXT);
CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(status, next_run_at);
CREATE INDEX IF NOT EXISTS idx_schedules_user ON schedules(user_id, status);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS receipts (
  id INTEGER PRIMARY KEY, turn_id TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_receipts_turn ON receipts(turn_id);

CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, text TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, sent_at TEXT);

CREATE TABLE IF NOT EXISTS coding_requests (
  id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
  instruction TEXT NOT NULL, target TEXT, status TEXT NOT NULL,
  created_at TEXT NOT NULL, detail TEXT);
CREATE INDEX IF NOT EXISTS idx_coding_requests_status ON coding_requests(status, id);

CREATE TABLE IF NOT EXISTS credential_requests (
  id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
  requested TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, detail TEXT);
CREATE INDEX IF NOT EXISTS idx_credential_requests_status ON credential_requests(status, id);

CREATE TABLE IF NOT EXISTS file_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  chat_id TEXT NOT NULL,
  path TEXT NOT NULL,
  status TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_file_requests_status ON file_requests(status, id);

CREATE TABLE IF NOT EXISTS command_requests (
  id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
  command TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, detail TEXT);
CREATE INDEX IF NOT EXISTS idx_command_requests_status ON command_requests(status, id);

CREATE TABLE IF NOT EXISTS missions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
  instruction TEXT NOT NULL, status TEXT NOT NULL,
  failures INTEGER NOT NULL DEFAULT 0, result TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_missions_status ON missions(status, id);
CREATE INDEX IF NOT EXISTS idx_missions_user ON missions(user_id, status);

CREATE TABLE IF NOT EXISTS facts_history (
  id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, key TEXT NOT NULL,
  old_value TEXT, new_value TEXT NOT NULL, resolution TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_facts_history_user ON facts_history(user_id, key);

CREATE TABLE IF NOT EXISTS user_email_accounts (
  user_id     TEXT    NOT NULL,
  label       TEXT    NOT NULL,
  imap_host   TEXT    NOT NULL,
  imap_port   INTEGER NOT NULL DEFAULT 993,
  imap_secure INTEGER NOT NULL DEFAULT 1,
  smtp_host   TEXT    NOT NULL,
  smtp_port   INTEGER NOT NULL DEFAULT 465,
  smtp_secure INTEGER NOT NULL DEFAULT 1,
  email_user  TEXT    NOT NULL,
  pass_enc    TEXT    NOT NULL,
  created_at  TEXT    NOT NULL,
  PRIMARY KEY (user_id, label));

CREATE TABLE IF NOT EXISTS email_enroll_requests (
  id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_email_enroll_status ON email_enroll_requests(status, id);

CREATE TABLE IF NOT EXISTS whatsapp_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inbound_id INTEGER,                      -- for drafts/outbound: the inbound replied to
    wa_chat_id TEXT NOT NULL,
    contact_name TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL,                 -- 'in' | 'out'
    text TEXT NOT NULL DEFAULT '',
    media_kind TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,                     -- in: pending|notified|answered ; out: draft|queued|sending|sent|failed
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS whatsapp_filter (
    kind TEXT NOT NULL,                       -- 'contact' | 'keyword'
    value TEXT NOT NULL,
    PRIMARY KEY (kind, value)
);
"""


async def connect(db_path: str, embedding_dim: int = 1024) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(db_path)
    await conn.enable_load_extension(True)
    await conn.load_extension(sqlite_vec.loadable_path())
    await conn.enable_load_extension(False)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA busy_timeout=5000;")
    await conn.executescript(_SCHEMA)
    await conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS recalls_vec "
        f"USING vec0(id INTEGER PRIMARY KEY, user_id TEXT partition, "
        f"embedding FLOAT[{embedding_dim}])"
    )
    await conn.commit()
    return conn


async def open_existing(db_path: str) -> aiosqlite.Connection:
    """Light connection for Ari's MCP server process: no sqlite-vec, no vector
    table — only the plain tables it reads/writes (schedules, facts, access,
    receipts, outbox, kv). WAL is a persistent DB property set by ``connect``."""
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA busy_timeout=5000;")
    await conn.executescript(_SCHEMA)
    await conn.commit()
    return conn
