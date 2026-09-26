from ari.config.settings import Settings


def test_tool_settings_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg")
    s = Settings(_env_file=None)
    assert s.mcp_config == "./mcp/servers.json"
    assert s.chat_timeout_seconds == 180
