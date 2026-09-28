from ari.application.credentials.needs import missing_inbound_secrets
from ari.application.skills.skill_manager import SkillStatus


def _s(name, state, missing, hooks):
    return SkillStatus(name, "0.1.0", state == "active", state, missing, hooks)


def test_collects_missing_secrets_of_needs_secrets_inbound_skills():
    statuses = [
        _s("groq_audio", "needs_secrets", ["GROQ_API_KEY"], ["inbound_transform", "outbound_transform"]),
        _s("other", "needs_secrets", ["OTHER_KEY"], ["outbound_transform"]),   # not inbound -> ignored
        _s("active_one", "active", [], ["inbound_transform"]),                  # active -> ignored
    ]
    assert missing_inbound_secrets(statuses) == ["GROQ_API_KEY"]


def test_empty_when_nothing_needs_inbound_secrets():
    assert missing_inbound_secrets([_s("a", "active", [], ["inbound_transform"])]) == []
    assert missing_inbound_secrets([]) == []
