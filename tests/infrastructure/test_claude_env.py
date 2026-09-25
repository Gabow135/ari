import os

from ari.infrastructure.claude_env import claude_cli_env


def test_no_token_keeps_host_login(tmp_path):
    assert claude_cli_env("", str(tmp_path / "cfg")) is None


def test_token_gives_ari_its_own_account_free_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")
    cfg = tmp_path / "cfg"
    env = claude_cli_env("tok-123", str(cfg))
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-123"
    assert env["CLAUDE_CONFIG_DIR"] == os.path.abspath(str(cfg))
    assert cfg.is_dir()
    assert "ANTHROPIC_API_KEY" not in env
    assert env.get("PATH") == os.environ.get("PATH")  # rest of the env is kept
