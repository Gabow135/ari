"""Regression test: WhatsApp tools are owner-only in CHAT context."""
from ari.domain.tools.ari_permissions import CHAT, HEARTBEAT, TASK, allowed_ari_tools

WA = ("whatsapp_pendientes", "whatsapp_responder", "whatsapp_enviar", "whatsapp_filtro")


def test_owner_chat_has_all_whatsapp_tools():
    allowed = allowed_ari_tools(is_owner=True, context=CHAT)
    assert all(t in allowed for t in WA)


def test_non_owner_chat_has_no_whatsapp_tools():
    allowed = allowed_ari_tools(is_owner=False, context=CHAT)
    assert all(t not in allowed for t in WA)


def test_whatsapp_absent_in_task_and_heartbeat():
    for ctx in (TASK, HEARTBEAT):
        allowed = allowed_ari_tools(is_owner=True, context=ctx)
        assert all(t not in allowed for t in WA)
