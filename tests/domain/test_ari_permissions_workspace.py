from ari.domain.tools.ari_permissions import (
    CHAT, HEARTBEAT, TASK, allowed_ari_tools,
)

_FILES_SQL = {"escribir_archivo", "leer_archivo", "listar_archivos",
              "borrar_archivo", "consultar_sql"}


def test_owner_chat_has_everything_including_ejecutar():
    allowed = set(allowed_ari_tools(True, CHAT))
    assert _FILES_SQL <= allowed
    assert "ejecutar" in allowed


def test_user_chat_has_files_and_sql_but_not_ejecutar():
    allowed = set(allowed_ari_tools(False, CHAT))
    assert _FILES_SQL <= allowed
    assert "ejecutar" not in allowed


def test_workspace_tools_absent_outside_chat():
    for ctx in (TASK, HEARTBEAT):
        allowed = set(allowed_ari_tools(True, ctx))
        assert not (_FILES_SQL & allowed)
        assert "ejecutar" not in allowed
