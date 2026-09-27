from ari.application.skills.skill_admin import SkillAdmin
from ari.application.skills.skill_manager import SkillStatus


class _Manager:
    def __init__(self, statuses):
        self._statuses = statuses
        self.enabled_calls = []

    def list(self):
        return self._statuses

    def set_enabled(self, name, enabled):
        self.enabled_calls.append((name, enabled))
        return any(s.name == name for s in self._statuses)


class _VaultWeb:
    def __init__(self):
        self.links = 0

    def new_link(self):
        self.links += 1
        return "https://192.168.0.5:8765/v/tok"


def _status(name, enabled=True, state="active", missing=None):
    return SkillStatus(name, "0.1.0", enabled, state, missing or [], ["inbound_transform"])


def test_list_text_lists_skills():
    admin = SkillAdmin(_Manager([_status("groq_audio")]), _VaultWeb())
    text = admin.list_text()
    assert "groq_audio" in text and "active" in text


def test_list_text_when_empty():
    assert "No hay skills" in SkillAdmin(_Manager([]), _VaultWeb()).list_text()


def test_enable_missing_secret_returns_link():
    vw = _VaultWeb()
    admin = SkillAdmin(_Manager([_status("groq_audio", state="needs_secrets", missing=["GROQ_API_KEY"])]), vw)
    out = admin.enable("groq_audio")
    assert vw.links == 1
    assert "https://192.168.0.5:8765/v/tok" in out
    assert "GROQ_API_KEY" in out


def test_enable_ready_skill_no_link():
    vw = _VaultWeb()
    admin = SkillAdmin(_Manager([_status("groq_audio", state="active")]), vw)
    out = admin.enable("groq_audio")
    assert vw.links == 0 and "Activé" in out


def test_enable_unknown_skill():
    admin = SkillAdmin(_Manager([]), _VaultWeb())
    assert "No existe" in admin.enable("nope")


def test_disable():
    m = _Manager([_status("groq_audio")])
    out = SkillAdmin(m, _VaultWeb()).disable("groq_audio")
    assert ("groq_audio", False) in m.enabled_calls and "Desactivé" in out
