from ari.domain.agent.capabilities import render_capabilities
from ari.domain.memory.entities import Fact, Recall, Summary

_WITH_OWNER = ("## Con quién hablas\n"
               "Estás hablando con tu creador (dueño de Ari). Tiene acceso a todo.")
_WITH_USER = ("## Con quién hablas\n"
              "Estás hablando con un usuario que tu creador aprobó. No tiene acceso a "
              "los comandos de administración; no los menciones.")


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
                     is_owner: bool = False) -> str:
        parts = [soul.strip() if soul and soul.strip() else self.SYSTEM_PREAMBLE,
                 _WITH_OWNER if is_owner else _WITH_USER,
                 render_capabilities(is_owner)]
        if facts:
            lines = "\n".join(f"- {f.key}: {f.value}" for f in facts)
            parts.append(f"## Lo que sabes del usuario\n{lines}")
        if summary:
            parts.append(f"## Resumen de la conversación anterior\n{summary.content}")
        if recalls:
            lines = "\n".join(f"- {r.content}" for r in recalls)
            parts.append(f"## Recuerdos relevantes\n{lines}")
        return "\n\n".join(parts)
