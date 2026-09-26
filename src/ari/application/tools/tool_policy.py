import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from ari.domain.tools.ari_permissions import ARI_SERVER, CHAT, allowed_ari_tools
from ari.domain.tools.toolset import WEB_TOOLS, Toolset, ToolsView, Turn, server_icon

TOOL_SEARCH = "ToolSearch"  # the CLI loads MCP tools lazily; they need ToolSearch


@dataclass(frozen=True)
class AriServerSpec:
    command: str
    args: tuple[str, ...]
    base_env: dict = field(default_factory=dict)


@asynccontextmanager
async def no_turn():
    yield None


class ToolPolicy:
    """Which tools one Claude call may use, decided by who Ari is talking to."""

    def __init__(self, registry, is_owner, ari: "AriServerSpec | None" = None, writer=None):
        self._registry, self._is_owner = registry, is_owner
        self._ari, self._writer = ari, writer

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

    @asynccontextmanager
    async def turn(self, user_id: str, context: str = CHAT, actor_name: str = "",
                   chat_id: str | None = None):
        """Tools for one call. With Ari's server configured, writes a per-turn MCP
        config whose ``ari`` entry carries who is acting — the model can't change
        it — and always deletes it when the call ends."""
        turn_id = uuid.uuid4().hex
        view = self.view(user_id)
        if self._ari is None or self._writer is None:
            yield Turn(self.for_user(user_id), view, turn_id)
            return
        owner = self._is_owner(user_id)
        servers = self._registry.resolved(owner)
        servers[ARI_SERVER] = {
            "command": self._ari.command, "args": list(self._ari.args),
            "env": {**self._ari.base_env, "ARI_ACTOR_ID": user_id,
                    "ARI_ACTOR_CHAT": chat_id or user_id, "ARI_ACTOR_NAME": actor_name,
                    "ARI_ROLE": "owner" if owner else "user", "ARI_CONTEXT": context,
                    "ARI_TURN_ID": turn_id}}
        path = self._writer.write(servers)
        if path is None:  # can't write the config: web only, never a stale file
            yield Turn(Toolset(WEB_TOOLS, WEB_TOOLS), view, turn_id)
            return
        builtin = (*WEB_TOOLS, TOOL_SEARCH)
        allowed = (*builtin,
                   *(f"mcp__{n}" for n in servers if n != ARI_SERVER),
                   *(f"mcp__{ARI_SERVER}__{t}" for t in allowed_ari_tools(owner, context)))
        try:
            yield Turn(Toolset(builtin, allowed, path), view, turn_id)
        finally:
            self._writer.remove(path)
