from types import SimpleNamespace

from ari.infrastructure.gateway.telegram_adapter import TelegramAdapter


def _update(text, user_id=42, chat_id=99):
    msg = SimpleNamespace(text=text, chat_id=chat_id,
                          from_user=SimpleNamespace(id=user_id))
    return SimpleNamespace(message=msg, effective_message=msg)


def test_to_incoming_maps_text():
    inc = TelegramAdapter.to_incoming(_update("hola"))
    assert inc.user_id == "42"
    assert inc.chat_id == "99"
    assert inc.text == "hola"


def test_to_incoming_skips_non_text():
    assert TelegramAdapter.to_incoming(_update(None)) is None
    assert TelegramAdapter.to_incoming(SimpleNamespace(
        message=None, effective_message=None)) is None


def test_split_text_respects_limit():
    parts = TelegramAdapter.split_text("x" * 9000, limit=4096)
    assert all(len(p) <= 4096 for p in parts)
    assert "".join(parts) == "x" * 9000
