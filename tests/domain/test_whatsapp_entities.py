from ari.domain.whatsapp.entities import InboundWhatsApp


def test_inbound_defaults_text_only():
    m = InboundWhatsApp(wa_chat_id="5939@s.whatsapp.net", contact_name="Juan", text="hola")
    assert m.media_kind == ""
    assert m.is_group is False


def test_inbound_media_placeholder_fields():
    m = InboundWhatsApp(wa_chat_id="g@g.us", contact_name="Grupo", text="",
                        media_kind="image", is_group=True)
    assert m.media_kind == "image"
    assert m.is_group is True
