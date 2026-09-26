from ari.domain.tools.toolset import WEB_TOOLS, Toolset, ToolsView

TOOL_SEARCH = "ToolSearch"  # the CLI loads MCP tools lazily; they need ToolSearch
_DB_HINTS = ("mysql", "sql", "db", "postgres", "maria")


def server_icon(name: str) -> str:
    return "🗄️" if any(h in name.lower() for h in _DB_HINTS) else "🔌"


class ToolPolicy:
    """Which tools one Claude call may use, decided by who Ari is talking to."""

    def __init__(self, registry, is_owner):
        self._registry, self._is_owner = registry, is_owner

    def for_user(self, user_id: str) -> Toolset:
        names, path = self._registry.servers_for(self._is_owner(user_id))
        if not names:
            return Toolset(WEB_TOOLS, WEB_TOOLS)
        builtin = (*WEB_TOOLS, TOOL_SEARCH)
        return Toolset(builtin, (*builtin, *(f"mcp__{n}" for n in names)), path)

    def view(self, user_id: str) -> ToolsView:
        servers = self._registry.descriptions(self._is_owner(user_id))
        lines = ["## Tus herramientas y conexiones",
                 "- 🌐 web: buscar y leer páginas."]
        lines += [f"- {server_icon(n)} {n}: {d}" for n, d in servers]
        return ToolsView("\n".join(lines), has_web=True, has_mcp=bool(servers))

    def status_text(self) -> str:
        lines = []
        for s in self._registry.status():
            who = "dueño" if s.access == "owner" else "todos"
            state = "✅ configurado" if s.ok else f"⚠️ {s.detail}"
            lines.append(f"{server_icon(s.name)} {s.name} — {s.description} ({who}) — {state}")
        lines.append("🌐 web — buscar y leer páginas (todos)")
        return "\n".join(lines)
