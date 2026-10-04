from ari.domain.agent.agent_service import AgentService
from ari.domain.memory.entities import Fact, Recall, Summary
from ari.domain.tools.toolset import ToolsView


def test_build_prompt_includes_all_layers():
    svc = AgentService()
    prompt = svc.build_prompt(
        facts=[Fact("u1", "name", "Gabo")],
        summary=Summary("u1", "Talked about the roadmap."),
        recalls=[Recall(1, "u1", "User prefers async Python.")],
    )
    assert "Gabo" in prompt
    assert "roadmap" in prompt
    assert "async Python" in prompt


def test_build_prompt_handles_empty_layers():
    svc = AgentService()
    prompt = svc.build_prompt(facts=[], summary=None, recalls=[])
    assert svc.SYSTEM_PREAMBLE in prompt


def test_soul_replaces_default_preamble():
    svc = AgentService()
    prompt = svc.build_prompt([], None, [], soul="Soy Ari, alma de prueba.")
    assert "alma de prueba" in prompt
    assert svc.SYSTEM_PREAMBLE not in prompt


def test_blank_soul_falls_back_to_default():
    svc = AgentService()
    assert svc.SYSTEM_PREAMBLE in svc.build_prompt([], None, [], soul="   ")


def test_owner_prompt_says_creator_and_lists_admin_commands():
    prompt = AgentService().build_prompt([], None, [], is_owner=True)
    assert "creador" in prompt.lower()
    assert "/restart" in prompt and "/code" in prompt


def test_user_prompt_hides_admin_commands():
    prompt = AgentService().build_prompt([], None, [], is_owner=False)
    assert "/restart" not in prompt and "/aprobar" not in prompt
    assert "creador" in prompt.lower()  # knows who created it, but isn't talking to them


def test_owner_prompt_names_the_person_when_known():
    prompt = AgentService().build_prompt([], None, [], is_owner=True, display_name="Gabriel")
    assert "Gabriel" in prompt
    assert "creador" in prompt.lower()


def test_user_prompt_names_the_person_when_known():
    prompt = AgentService().build_prompt([], None, [], is_owner=False, display_name="María")
    assert "María" in prompt
    assert "/restart" not in prompt  # naming the person must not leak admin commands


def test_prompt_without_display_name_keeps_generic_role_line():
    # Regression: background runs (scheduled tasks, heartbeat, missions) carry no
    # name, so the role line must stay exactly as it was.
    prompt = AgentService().build_prompt([], None, [], is_owner=True)
    assert "Estás hablando con tu creador (dueño de Ari)" in prompt


def test_extra_section_is_appended():
    prompt = AgentService().build_prompt([], None, [], extra="## Fecha y hora actual\nhoy")
    assert "## Fecha y hora actual\nhoy" in prompt


def test_tools_view_is_rendered_and_drops_web_limitation():
    view = ToolsView("## Tus herramientas y conexiones\n- 🌐 web", has_web=True, has_mcp=False)
    prompt = AgentService().build_prompt([], None, [], is_owner=True, tools=view)
    assert "## Tus herramientas y conexiones" in prompt
    assert "no puedes navegar la web" not in prompt


def test_without_tools_view_prompt_is_unchanged():
    prompt = AgentService().build_prompt([], None, [], is_owner=True)
    assert "no puedes navegar la web" in prompt
