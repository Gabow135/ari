from ari.config.settings import Settings


def test_whatsapp_defaults_disabled(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    s = Settings()
    assert s.whatsapp_enabled is False
    assert s.whatsapp_session_dir == "~/.ari/whatsapp"
    assert s.whatsapp_send_min_delay_seconds == 3
    assert s.whatsapp_number == ""
