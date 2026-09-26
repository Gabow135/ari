from ari.application.tools.tool_policy import ToolPolicy
from ari.domain.tools.toolset import WEB_TOOLS
from ari.infrastructure.tools.mcp_registry import ServerStatus


class FakeRegistry:
    def __init__(self, owner=(), users=()):
        self._owner, self._users = owner, users

    def servers_for(self, is_owner):
        names = tuple(n for n, _ in (self._owner if is_owner else self._users))
        return names, ("/cfg/owner.json" if is_owner else "/cfg/users.json") if names else None

    def descriptions(self, is_owner):
        return list(self._owner if is_owner else self._users)

    def status(self):
        return [ServerStatus("google", "Gmail del creador", "owner", True, ""),
                ServerStatus("mysql", "Base (solo lectura)", "owner", False, "falta ARI_MYSQL_PASS")]


def _policy(owner=(), users=()):
    return ToolPolicy(FakeRegistry(owner, users), is_owner=lambda uid: uid == "42")


def test_user_without_servers_gets_web_only():
    t = _policy(owner=[("google", "Gmail")]).for_user("7")
    assert t.builtin_tools == WEB_TOOLS and t.allowed_tools == WEB_TOOLS
    assert t.mcp_config_path is None


def test_owner_with_servers_gets_toolsearch_and_server_allowlist():
    t = _policy(owner=[("google", "Gmail"), ("mysql", "Base")]).for_user("42")
    assert t.builtin_tools == (*WEB_TOOLS, "ToolSearch")
    assert t.allowed_tools == (*WEB_TOOLS, "ToolSearch", "mcp__google", "mcp__mysql")
    assert t.mcp_config_path == "/cfg/owner.json"


def test_view_lists_web_and_role_connections():
    v = _policy(owner=[("google", "Gmail del creador")]).view("42")
    assert v.has_web and v.has_mcp
    assert "🌐 web" in v.text and "🔌 google: Gmail del creador" in v.text
    u = _policy(owner=[("google", "Gmail del creador")]).view("7")
    assert u.has_web and not u.has_mcp and "google" not in u.text


def test_status_text():
    text = _policy().status_text()
    assert "🔌 google — Gmail del creador (dueño) — ✅ configurado" in text
    assert "🗄️ mysql — Base (solo lectura) (dueño) — ⚠️ falta ARI_MYSQL_PASS" in text
    assert "🌐 web — buscar y leer páginas (todos)" in text
