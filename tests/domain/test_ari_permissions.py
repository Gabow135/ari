import pytest

from ari.domain.tools.ari_permissions import ARI_TOOLS, CHAT, HEARTBEAT, TASK, allowed_ari_tools

USER_CHAT = {"agendar", "listar_agenda", "cancelar", "recordar_dato", "olvidar_dato",
             "ver_datos", "compartir", "ver_permisos", "revocar_permiso",
             "conectar_correo", "mis_correos", "olvidar_correo",
             "buscar_correos", "leer_correo",
             "escribir_archivo", "leer_archivo", "listar_archivos",
             "borrar_archivo", "consultar_sql", "leer_documento"}


def test_catalogue():
    assert ARI_TOOLS == ("agendar", "listar_agenda", "cancelar", "recordar_dato",
                         "olvidar_dato", "ver_datos", "aprobar_acceso", "revocar_acceso",
                         "ver_accesos", "enviar_mensaje", "proponer_codigo", "proponer_comando",
                         "asignar_mision",
                         "ver_misiones", "cancelar_mision", "pedir_credenciales",
                         "ver_skills", "activar_skill", "desactivar_skill",
                         "compartir", "ver_permisos", "revocar_permiso",
                         "conectar_correo", "mis_correos", "olvidar_correo",
                         "buscar_correos", "leer_correo",
                         "escribir_archivo", "leer_archivo", "listar_archivos",
                         "borrar_archivo", "consultar_sql", "ejecutar", "leer_documento")


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


def test_proponer_comando_is_owner_chat_only():
    assert "proponer_comando" in allowed_ari_tools(True, CHAT)
    for owner, context in [(False, CHAT), (True, TASK), (True, HEARTBEAT), (False, TASK)]:
        assert "proponer_comando" not in allowed_ari_tools(owner, context)


_GRANT_TOOLS = {"compartir", "ver_permisos", "revocar_permiso"}


def test_grant_tools_allowed_for_any_user_in_chat():
    assert _GRANT_TOOLS <= set(allowed_ari_tools(False, CHAT))
    assert _GRANT_TOOLS <= set(allowed_ari_tools(True, CHAT))


def test_grant_tools_denied_in_task_and_heartbeat():
    assert _GRANT_TOOLS.isdisjoint(allowed_ari_tools(False, TASK))
    assert _GRANT_TOOLS.isdisjoint(allowed_ari_tools(True, HEARTBEAT))


def test_email_reading_allowed_for_owner_chat():
    allowed = allowed_ari_tools(True, CHAT)
    assert "buscar_correos" in allowed and "leer_correo" in allowed


def test_email_reading_allowed_for_user_chat():
    allowed = allowed_ari_tools(False, CHAT)
    assert "buscar_correos" in allowed and "leer_correo" in allowed


def test_email_reading_absent_in_task_and_heartbeat():
    for ctx in (TASK, HEARTBEAT):
        allowed = allowed_ari_tools(True, ctx)
        assert "buscar_correos" not in allowed and "leer_correo" not in allowed
