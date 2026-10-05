from ari.domain.agent.capabilities import (
    CAPABILITIES,
    LIMITATIONS,
    limitations,
    menu_commands,
    render_capabilities,
)


def test_owner_sees_every_capability_and_limitations():
    text = render_capabilities(is_owner=True)
    for cap in CAPABILITIES:
        assert cap.summary in text
    for limit in LIMITATIONS:
        assert limit in text


def test_non_owner_does_not_see_admin_commands():
    text = render_capabilities(is_owner=False)
    for cap in CAPABILITIES:
        if cap.owner_only:
            assert cap.summary not in text
            if cap.command:
                assert f"/{cap.command}" not in text
    assert "/start" in text


def test_menu_is_derived_from_registry():
    assert [c for c, _ in menu_commands(owner=False)] == ["start", "recordatorios"]
    owner = [c for c, _ in menu_commands(owner=True)]
    assert set(owner) == {"start", "recordatorios", "code", "aprobar", "revocar",
                          "accesos", "conexiones", "vault", "restart", "stop",
                          "skills", "skill_on", "skill_off"}
    assert owner[0] == "start"


def test_owner_is_told_to_code_from_natural_language():
    low = render_capabilities(is_owner=True).lower()
    # The owner triggers coding by asking in plain language; /code is only an
    # optional shortcut and must never be presented as required.
    assert "no necesita escribir /code" in low
    assert "proponer_codigo" in low


def test_owner_is_told_not_to_run_commands_unprompted():
    low = render_capabilities(is_owner=True).lower()
    # Commands run only when the owner explicitly asks; Ari must never reach for
    # the shell on its own initiative to progress a task or fetch data.
    assert "solo cuando te lo pida" in low
    assert "nunca propongas ni corras un comando por tu cuenta" in low


def test_proactivity_limitations_removed():
    text = " ".join(LIMITATIONS)
    assert "iniciativa propia" not in text and "recordatorios" not in text


def test_vault_command_is_owner_only():
    owner = dict(menu_commands(owner=True))
    public = dict(menu_commands(owner=False))
    assert "vault" in owner and "vault" not in public


def test_vault_capability_is_described_to_owner():
    assert "/vault" in render_capabilities(is_owner=True)
    assert "/vault" not in render_capabilities(is_owner=False)


def test_limitations_adapt_to_tools():
    assert limitations(False, False) == LIMITATIONS
    web_only = " ".join(limitations(True, False))
    assert "navegar la web" not in web_only and "conexiones MCP" in web_only
    with_mcp = " ".join(limitations(True, True))
    assert "Todavía no tienes conexiones MCP" not in with_mcp
    assert "Solo tienes las conexiones listadas" in with_mcp


def test_workspace_capability_visible_to_all_users():
    text = render_capabilities(is_owner=False)
    assert "espacio de trabajo privado" in text
    assert "leer_documento" in text


def test_workspace_ejecutar_only_for_owner():
    owner_text = render_capabilities(is_owner=True)
    user_text = render_capabilities(is_owner=False)
    assert "en tu espacio de trabajo con ejecutar" in owner_text
    assert "en tu espacio de trabajo con ejecutar" not in user_text


def test_no_tools_limitation_no_longer_claims_cant_read_files():
    no_tools_limitations = " ".join(limitations(has_web=False, has_mcp=False))
    assert "leer archivos" not in no_tools_limitations


def test_menu_commands_unchanged_by_new_capabilities():
    # Neither new Capability has a command, so menu lists must stay the same.
    assert [c for c, _ in menu_commands(owner=False)] == ["start", "recordatorios"]
    owner = [c for c, _ in menu_commands(owner=True)]
    assert set(owner) == {"start", "recordatorios", "code", "aprobar", "revocar",
                          "accesos", "conexiones", "vault", "restart", "stop",
                          "skills", "skill_on", "skill_off"}
