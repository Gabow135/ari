from types import SimpleNamespace

from ari.infrastructure.whatsapp.neonize_adapter import inbound_from_event


def _event(conversation="", image=False, pushname="Juan", is_group=False,
           chat="549111@s.whatsapp.net"):
    msg = SimpleNamespace(conversation=conversation, extendedTextMessage=None,
                          imageMessage=SimpleNamespace(caption="") if image else None,
                          audioMessage=None, videoMessage=None,
                          documentMessage=None, stickerMessage=None)
    source = SimpleNamespace(IsGroup=is_group, chat=chat)
    info = SimpleNamespace(MessageSource=source, Pushname=pushname)
    return SimpleNamespace(Message=msg, Info=info)


def test_text_message_maps_to_inbound():
    m = inbound_from_event(_event(conversation="hola"))
    assert m.text == "hola" and m.contact_name == "Juan"
    assert m.wa_chat_id == "549111@s.whatsapp.net" and m.media_kind == "" and m.is_group is False


def test_image_message_sets_media_kind_and_empty_text():
    m = inbound_from_event(_event(image=True, conversation=""))
    assert m.media_kind == "image" and m.text == ""
