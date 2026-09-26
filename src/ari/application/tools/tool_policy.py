from ari.domain.tools.toolset import WEB_TOOLS, Toolset, ToolsView, server_icon

TOOL_SEARCH = "ToolSearch"  # the CLI loads MCP tools lazily; they need ToolSearch


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
            if s.access == "owner":
                who = "dueño"
            elif s.access == "users":
                who = "todos"
            else:
                who = s.access  # unexpected value: show it as-is, never hide it
            if s.ok:
                state = "✅ configurado"
            elif s.detail.startswith("falta "):
                state = f"⚠️ {s.detail}"  # a missing env var: fixable without editing servers.json
            else:
                state = f"⛔ {s.detail}"  # invalid access/name or missing launcher
            lines.append(f"{server_icon(s.name)} {s.name} — {s.description} ({who}) — {state}")
        lines.append("🌐 web — buscar y leer páginas (todos)")
        return "\n".join(lines)
