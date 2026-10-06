from types import SimpleNamespace

from ari.infrastructure.whatsapp.neonize_adapter import inbound_from_event


def _event(conversation="", image=False, pushname="Juan", is_group=False,
           user="549111", server="s.whatsapp.net", extended_text=None):
    ext = None
    if extended_text:
        ext = SimpleNamespace(text=extended_text)
    msg = SimpleNamespace(conversation=conversation, extendedTextMessage=ext,
                          imageMessage=SimpleNamespace(caption="") if image else None,
                          audioMessage=None, videoMessage=None,
                          documentMessage=None, stickerMessage=None)
    # Mirrors neonize's proto shape: MessageSource.Chat is a JID (User + Server).
    chat = SimpleNamespace(User=user, Server=server)
    source = SimpleNamespace(IsGroup=is_group, Chat=chat)
    info = SimpleNamespace(MessageSource=source, Pushname=pushname)
    return SimpleNamespace(Message=msg, Info=info)


def test_text_message_maps_to_inbound():
    m = inbound_from_event(_event(conversation="hola"))
    assert m.text == "hola" and m.contact_name == "Juan"
    assert m.wa_chat_id == "549111@s.whatsapp.net" and m.media_kind == "" and m.is_group is False


def test_image_message_sets_media_kind_and_empty_text():
    m = inbound_from_event(_event(image=True, conversation=""))
    assert m.media_kind == "image" and m.text == ""


def test_extended_text_message_fallback():
    """When conversation is empty, use extendedTextMessage.text."""
    m = inbound_from_event(_event(conversation="", extended_text="hola"))
    assert m.text == "hola"


def test_pushname_empty_fallback():
    """When pushname is empty, use chat number before '@'."""
    m = inbound_from_event(_event(pushname="", user="549111"))
    assert m.contact_name == "549111"


def test_group_message():
    """When is_group is True, propagate to InboundWhatsApp."""
    m = inbound_from_event(_event(is_group=True))
    assert m.is_group is True


def test_unknown_media_and_empty_text():
    """When no media and empty text, both fields should be empty."""
    m = inbound_from_event(_event(conversation="", image=False))
    assert m.media_kind == "" and m.text == ""
