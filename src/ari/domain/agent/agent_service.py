from ari.domain.memory.entities import Fact, Recall, Summary


class AgentService:
    SYSTEM_PREAMBLE = (
        "You are Ari, a helpful, concise assistant. "
        "Use the user's known facts and recalled context when relevant. "
        "If you are unsure, say so."
    )

    def build_prompt(self, facts: list[Fact], summary: Summary | None,
                     recalls: list[Recall]) -> str:
        parts = [self.SYSTEM_PREAMBLE]
        if facts:
            lines = "\n".join(f"- {f.key}: {f.value}" for f in facts)
            parts.append(f"Known facts about the user:\n{lines}")
        if summary:
            parts.append(f"Summary of earlier conversation:\n{summary.content}")
        if recalls:
            lines = "\n".join(f"- {r.content}" for r in recalls)
            parts.append(f"Relevant recalled context:\n{lines}")
        return "\n\n".join(parts)
