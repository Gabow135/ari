import pytest

from ari.domain.tools.ari_permissions import (
    ARI_TOOLS, CHAT, HEARTBEAT, TASK, allowed_ari_tools)

USER_CHAT = {"agendar", "listar_agenda", "cancelar", "recordar_dato", "olvidar_dato",
             "ver_datos"}


def test_catalogue():
    assert ARI_TOOLS == ("agendar", "listar_agenda", "cancelar", "recordar_dato",
                         "olvidar_dato", "ver_datos", "aprobar_acceso", "revocar_acceso",
                         "ver_accesos", "enviar_mensaje", "proponer_codigo")


def test_chat_permissions():
    assert set(allowed_ari_tools(False, CHAT)) == USER_CHAT
    assert allowed_ari_tools(True, CHAT) == ARI_TOOLS


@pytest.mark.parametrize("owner", [False, True])
def test_task_is_read_only(owner):
    assert allowed_ari_tools(owner, TASK) == ("listar_agenda", "ver_datos")


def test_heartbeat_read_only_plus_owner_accesses():
    assert allowed_ari_tools(False, HEARTBEAT) == ("listar_agenda", "ver_datos")
    assert allowed_ari_tools(True, HEARTBEAT) == ("listar_agenda", "ver_datos", "ver_accesos")


def test_unknown_context_gets_nothing():
    assert allowed_ari_tools(True, "otro") == ()


def test_proponer_codigo_is_owner_chat_only():
    assert "proponer_codigo" in allowed_ari_tools(True, CHAT)
    for owner, context in [(False, CHAT), (True, TASK), (True, HEARTBEAT), (False, TASK)]:
        assert "proponer_codigo" not in allowed_ari_tools(owner, context)
