from types import SimpleNamespace

from ari.infrastructure.gateway.telegram_adapter import media_to_text


class _Manager:
    def __init__(self, reply="ok"):
        self.reply, self.seen = reply, None

    async def run_inbound(self, raw, is_owner):
        self.seen = raw
        return self.reply


def _update(*, photo=None, document=None, caption=None):
    msg = SimpleNamespace(photo=photo, document=document, caption=caption,
                          from_user=SimpleNamespace(id=7), chat_id=42)
    return SimpleNamespace(effective_message=msg, message=msg)


async def test_photo_takes_largest_and_carries_caption():
    small = SimpleNamespace(file_id="s")
    large = SimpleNamespace(file_id="l")
    async def download(media):
        assert media is large   # largest PhotoSize
        return b"JPG"
    m = _Manager("un gato")
    out = await media_to_text(_update(photo=[small, large], caption="¿qué es?"), download, m, True)
    assert out == "un gato"
    assert m.seen.attachment.kind == "photo" and m.seen.attachment.mime == "image/jpeg"
    assert m.seen.attachment.data == b"JPG" and m.seen.text == "¿qué es?"


async def test_document_carries_mime_and_filename():
    doc = SimpleNamespace(mime_type="application/pdf", file_name="report.pdf")
    async def download(media):
        assert media is doc
        return b"PDF"
    m = _Manager("texto")
    out = await media_to_text(_update(document=doc), download, m, True)
    assert out == "texto"
    assert m.seen.attachment.kind == "document"
    assert m.seen.attachment.mime == "application/pdf"
    assert m.seen.attachment.filename == "report.pdf"


async def test_image_mime_document_routes_as_photo():
    doc = SimpleNamespace(mime_type="image/png", file_name="pic.png")
    async def download(media):
        return b"PNG"
    m = _Manager("x")
    await media_to_text(_update(document=doc), download, m, True)
    assert m.seen.attachment.kind == "photo" and m.seen.attachment.mime == "image/png"


async def test_none_when_no_media():
    async def download(media):
        raise AssertionError("should not download")
    assert await media_to_text(_update(), download, _Manager(), True) is None
