import json
import os

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
