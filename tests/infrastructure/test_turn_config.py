import json
import os
import time

from ari.infrastructure.tools.turn_config import TurnConfigWriter


def test_write_and_remove(tmp_path):
    w = TurnConfigWriter(str(tmp_path / "mcp"))
    path = w.write({"ari": {"command": "python", "env": {"ARI_TURN_ID": "t1"}}})
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["mcpServers"]["ari"]["env"]["ARI_TURN_ID"] == "t1"
    assert os.path.basename(path).startswith("turn-")
    assert [p for p in os.listdir(tmp_path / "mcp") if p.endswith(".tmp")] == []
    if os.name != "nt":
        assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    w.remove(path)
    assert not os.path.exists(path)
    w.remove(path)  # idempotent


def test_write_failure_returns_none(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert TurnConfigWriter(str(blocker)).write({"a": {}}) is None  # out_dir is a file


def test_sweep_removes_only_old_orphaned_turn_configs(tmp_path):
    mcp_dir = tmp_path / "mcp"
    mcp_dir.mkdir()
    old_json = mcp_dir / "turn-aaa.json"
    old_tmp = mcp_dir / "turn-bbb.json.tmp"
    recent_json = mcp_dir / "turn-ccc.json"
    other = mcp_dir / "owner.json"
    for f in (old_json, old_tmp, recent_json, other):
        f.write_text("{}")
    old_time = time.time() - 700
    os.utime(old_json, (old_time, old_time))
    os.utime(old_tmp, (old_time, old_time))

    removed = TurnConfigWriter(str(mcp_dir)).sweep(older_than_seconds=600)

    assert removed == 2
    assert not old_json.exists()
    assert not old_tmp.exists()
    assert recent_json.exists()
    assert other.exists()  # never touches owner.json/users.json


def test_sweep_on_missing_dir_returns_zero(tmp_path):
    assert TurnConfigWriter(str(tmp_path / "missing")).sweep() == 0
