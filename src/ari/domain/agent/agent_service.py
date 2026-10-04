from ari.domain.agent.capabilities import render_capabilities
from ari.domain.memory.entities import Fact, Recall, Summary
from ari.domain.tools.toolset import ToolsView

_WITH_OWNER = ("## Con quién hablas\n"
               "Estás hablando con tu creador (dueño de Ari). Tiene acceso a todo.")
_WITH_USER = ("## Con quién hablas\n"
              "Estás hablando con un usuario que tu creador aprobó. No tiene acceso a "
              "los comandos de administración; no los menciones.")


def _who(is_owner: bool, display_name: str) -> str:
    """The "Con quién hablas" section. When the sender's name is known it is
    named so Ari recognizes the person; otherwise the generic role line stays
    (background runs — scheduled tasks, heartbeat, missions — carry no name)."""
    name = (display_name or "").strip()
    if is_owner:
        if name:
            return ("## Con quién hablas\n"
                    f"Estás hablando con tu creador, {name} (dueño de Ari). "
                    "Tiene acceso a todo.")
        return _WITH_OWNER
    if name:
        return ("## Con quién hablas\n"
                f"Estás hablando con {name}, un usuario que tu creador aprobó. "
                "No tiene acceso a los comandos de administración; no los menciones.")
    return _WITH_USER


class AgentService:
    # Used when no soul file is available.
    SYSTEM_PREAMBLE = (
        "Eres Ari, un asistente virtual que ayuda a su creador y a quien lo use a "
        "resolver sus tareas. Hablas en español neutro, tuteando, de forma cercana y "
        "concisa. Usa lo que sabes del usuario cuando sea relevante. Si no estás "
        "seguro de algo, dilo."
    )

    def build_prompt(self, facts: list[Fact], summary: Summary | None,
                     recalls: list[Recall], soul: str | None = None,
                     is_owner: bool = False, extra: str | None = None,
                     tools: ToolsView | None = None, display_name: str = "") -> str:
        parts = [soul.strip() if soul and soul.strip() else self.SYSTEM_PREAMBLE,
                 _who(is_owner, display_name),
                 render_capabilities(is_owner,
                                     has_web=bool(tools and tools.has_web),
                                     has_mcp=bool(tools and tools.has_mcp),
                                     has_degraded=bool(tools and tools.has_degraded))]
        if tools:
            parts.append(tools.text)
        if extra:
            parts.append(extra)
        if facts:
            lines = "\n".join(f"- {f.key}: {f.value}" for f in facts)
            parts.append(f"## Lo que sabes del usuario\n{lines}")
        if summary:
            parts.append(f"## Resumen de la conversación anterior\n{summary.content}")
        if recalls:
            lines = "\n".join(f"- {r.content}" for r in recalls)
            parts.append(f"## Recuerdos relevantes\n{lines}")
        return "\n\n".join(parts)
