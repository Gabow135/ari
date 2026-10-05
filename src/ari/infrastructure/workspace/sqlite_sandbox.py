import asyncio
import sqlite3
import threading
from dataclasses import dataclass, field


@dataclass
class SqlResult:
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    rowcount: int = 0
    truncated: bool = False


def _authorizer(action, arg1, arg2, db_name, source):
    # Block reaching other files (ATTACH/DETACH) and loading native code.
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and arg2 == "load_extension":
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _run_sync(db_path: str, sql: str, timeout: float, max_rows: int) -> SqlResult:
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit
    conn.set_authorizer(_authorizer)
    timer = threading.Timer(timeout, conn.interrupt)
    timer.start()
    try:
        cur = conn.execute(sql)  # raises on multiple statements — one op per call
        if cur.description is None:  # DDL/DML
            return SqlResult(rowcount=max(cur.rowcount, 0))
        cols = [d[0] for d in cur.description]
        fetched = cur.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        rows = fetched[:max_rows]
        return SqlResult(columns=cols, rows=rows, rowcount=len(rows), truncated=truncated)
    finally:
        timer.cancel()
        conn.close()


class SqliteSandbox:
    """Runs ONE SQL statement against a jailed sqlite file with an authorizer
    (no ATTACH/DETACH/load_extension), a wall-clock interrupt, and a row cap."""

    def __init__(self, *, timeout: float = 5.0, max_rows: int = 1000):
        self._timeout, self._max_rows = timeout, max_rows

    async def run(self, db_path: str, sql: str) -> SqlResult:
        return await asyncio.to_thread(
            _run_sync, db_path, (sql or "").strip(), self._timeout, self._max_rows)
