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
