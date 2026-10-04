import os

from ari.infrastructure.claude_env import claude_cli_env, with_mcp_startup_timeout


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


def test_mcp_startup_timeout_sets_env_in_milliseconds():
    # No login env: still returns a usable env (a copy of os.environ) with the
    # MCP startup timeout set, so a hung server fails fast instead of burning the turn.
    env = with_mcp_startup_timeout(None, 20)
    assert env["MCP_TIMEOUT"] == "20000"  # seconds -> milliseconds
    assert env.get("PATH") == os.environ.get("PATH")


def test_mcp_startup_timeout_layers_onto_existing_env_without_mutating_it():
    base = {"CLAUDE_CODE_OAUTH_TOKEN": "tok", "FOO": "bar"}
    env = with_mcp_startup_timeout(base, 30)
    assert env["MCP_TIMEOUT"] == "30000"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"
    assert env["FOO"] == "bar"
    assert "MCP_TIMEOUT" not in base  # input is not mutated
