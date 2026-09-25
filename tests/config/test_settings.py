from ari.config.settings import Settings


def test_defaults_and_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-test")
    monkeypatch.setenv("ARI_RECALL_TOP_K", "7")
    s = Settings()
    assert s.anthropic_api_key == "sk-test"
    assert s.telegram_bot_token == "tg-test"
    assert s.model == "claude-sonnet-4-6"
    assert s.recall_top_k == 7
