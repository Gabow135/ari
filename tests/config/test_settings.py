from ari.config.settings import Settings


def test_defaults_and_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-test")
    monkeypatch.setenv("ARI_RECALL_TOP_K", "7")
    s = Settings()
    assert s.telegram_bot_token == "tg-test"
    assert s.model == "claude-sonnet-4-6"
    assert s.recall_top_k == 7
    assert s.claude_bin == "claude"


def test_vault_settings_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    s = Settings(_env_file=None)
    assert s.vault_key == ""
    assert s.vault_path == "~/.ari/vault.enc"


def test_vault_key_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("ARI_VAULT_KEY", "abc")
    assert Settings(_env_file=None).vault_key == "abc"


def test_vault_web_settings_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    from ari.config.settings import Settings
    s = Settings(_env_file=None)
    assert s.vault_web_port == 8765
    assert s.vault_web_ttl_minutes == 10
    assert s.vault_web_bind == "0.0.0.0"
