from ari.domain.agent.capabilities import (
    CAPABILITIES,
    LIMITATIONS,
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
    assert [c for c, _ in menu_commands(owner=False)] == ["start"]
    owner = [c for c, _ in menu_commands(owner=True)]
    assert set(owner) == {"start", "code", "aprobar", "revocar", "accesos", "restart", "stop"}
    assert owner[0] == "start"
