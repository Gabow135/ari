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
