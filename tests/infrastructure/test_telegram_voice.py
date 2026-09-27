from types import SimpleNamespace

from ari.infrastructure.gateway.telegram_adapter import voice_to_text


class _Manager:
    def __init__(self, reply):
        self.reply = reply
        self.seen = None

    async def run_inbound(self, raw, is_owner):
        self.seen = raw
        return self.reply


def _update_with_voice():
    voice = SimpleNamespace(mime_type="audio/ogg", duration=3)
    msg = SimpleNamespace(voice=voice, audio=None, from_user=SimpleNamespace(id=7), chat_id=42)
    return SimpleNamespace(effective_message=msg, message=msg)


async def test_voice_to_text_downloads_and_transcribes():
    async def download(media):
        return b"OGGBYTES"
    m = _Manager("hola desde audio")
    text = await voice_to_text(_update_with_voice(), download, m, is_owner=True)
    assert text == "hola desde audio"
    assert m.seen.attachment.kind == "voice"
    assert m.seen.attachment.data == b"OGGBYTES"
    assert m.seen.user_id == "7" and m.seen.chat_id == "42"


async def test_voice_to_text_none_when_no_media():
    msg = SimpleNamespace(voice=None, audio=None, from_user=SimpleNamespace(id=7), chat_id=42)
    upd = SimpleNamespace(effective_message=msg, message=msg)
    async def download(media):
        raise AssertionError("should not download")
    assert await voice_to_text(upd, download, _Manager("x"), is_owner=True) is None
