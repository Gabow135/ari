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
                          "accesos", "restart", "stop"}
    assert owner[0] == "start"


def test_proactivity_limitations_removed():
    text = " ".join(LIMITATIONS)
    assert "iniciativa propia" not in text and "recordatorios" not in text


def test_limitations_adapt_to_tools():
    assert limitations(False, False) == LIMITATIONS
    web_only = " ".join(limitations(True, False))
    assert "navegar la web" not in web_only and "conexiones MCP" in web_only
    with_mcp = " ".join(limitations(True, True))
    assert "Todavía no tienes conexiones MCP" not in with_mcp
    assert "Solo tienes las conexiones listadas" in with_mcp
