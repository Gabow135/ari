from ari.config.settings import Settings


def test_concurrency_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg")
    s = Settings(_env_file=None)
    assert s.max_concurrent_chats == 8
    assert s.max_background_agents == 3


def test_concurrency_overridable_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg")
    monkeypatch.setenv("ARI_MAX_CONCURRENT_CHATS", "16")
    monkeypatch.setenv("ARI_MAX_BACKGROUND_AGENTS", "5")
    s = Settings(_env_file=None)
    assert s.max_concurrent_chats == 16
    assert s.max_background_agents == 5
