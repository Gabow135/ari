from ari.domain.agent.agent_service import AgentService
from ari.domain.memory.entities import Fact, Recall, Summary


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
