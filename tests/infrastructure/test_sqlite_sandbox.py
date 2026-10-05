import sqlite3

import pytest

from ari.infrastructure.workspace.sqlite_sandbox import SqliteSandbox


async def test_ddl_dml_then_select(tmp_path):
    db = str(tmp_path / "data.sqlite")
    sb = SqliteSandbox()
    await sb.run(db, "CREATE TABLE t (a INTEGER, b TEXT)")
    r = await sb.run(db, "INSERT INTO t VALUES (1, 'uno'), (2, 'dos')")
    assert r.rowcount == 2 and r.columns == []
    r = await sb.run(db, "SELECT a, b FROM t ORDER BY a")
    assert r.columns == ["a", "b"]
    assert r.rows == [(1, "uno"), (2, "dos")]


async def test_attach_is_denied(tmp_path):
    db = str(tmp_path / "data.sqlite")
    sb = SqliteSandbox()
    with pytest.raises(sqlite3.DatabaseError):
        await sb.run(db, f"ATTACH DATABASE '{tmp_path / 'other.sqlite'}' AS other")


async def test_row_cap_truncates(tmp_path):
    db = str(tmp_path / "data.sqlite")
    sb = SqliteSandbox(max_rows=2)
    await sb.run(db, "CREATE TABLE t (a INTEGER)")
    await sb.run(db, "INSERT INTO t VALUES (1),(2),(3),(4)")
    r = await sb.run(db, "SELECT a FROM t ORDER BY a")
    assert r.rows == [(1,), (2,)] and r.truncated is True


async def test_statement_timeout(tmp_path):
    db = str(tmp_path / "data.sqlite")
    sb = SqliteSandbox(timeout=0.1)
    runaway = ("SELECT count(*) FROM ("
               "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) "
               "SELECT x FROM c)")
    with pytest.raises(sqlite3.Error):
        await sb.run(db, runaway)
