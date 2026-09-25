from ari.config.settings import Settings


def test_proactivity_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg")
    s = Settings(_env_file=None)
    assert s.timezone == "America/Guayaquil"
    assert s.quiet_hours == "22-7"
    assert s.heartbeat_minutes == 60
    assert s.max_items_per_user == 20
