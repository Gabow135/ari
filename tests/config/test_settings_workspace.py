from ari.config.settings import Settings


def test_workspaces_dir_default(monkeypatch):
    monkeypatch.delenv("ARI_WORKSPACES_DIR", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    assert Settings().workspaces_dir == "~/.ari/workspaces"


def test_workspaces_dir_override(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("ARI_WORKSPACES_DIR", "/data/ws")
    assert Settings().workspaces_dir == "/data/ws"
