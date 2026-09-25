from ari.config.settings import Settings


def test_phase2_settings(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg")
    monkeypatch.setenv("ARI_OWNER_IDS", "42, 7 ,100")
    monkeypatch.setenv("ARI_ALLOWED_ROOT", "/tmp/projects")
    s = Settings()
    assert s.owner_id_set == {"42", "7", "100"}
    assert s.allowed_root == "/tmp/projects"
    assert s.coder_model == "claude-sonnet-4-6"
    assert s.coding_timeout_seconds == 900


def test_owner_id_set_empty_when_unset(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg")
    monkeypatch.delenv("ARI_OWNER_IDS", raising=False)
    # _env_file=None: ignore the developer's real .env, which may set owners.
    assert Settings(_env_file=None).owner_id_set == set()
