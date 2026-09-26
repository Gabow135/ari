from dataclasses import dataclass

WEB_TOOLS = ("WebSearch", "WebFetch")


@dataclass(frozen=True)
class Toolset:
    """What one Claude call may use: built-in tools, the permission allowlist,
    and the resolved MCP config file (None when the caller has no MCP servers)."""
    builtin_tools: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    mcp_config_path: str | None = None


@dataclass(frozen=True)
class ToolsView:
    """How the caller's tools are described to Ari in its prompt."""
    text: str
    has_web: bool
    has_mcp: bool
