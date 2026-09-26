"""Which of Ari's own tools each role may use in each context (pure)."""

CHAT, TASK, HEARTBEAT = "chat", "task", "heartbeat"
ARI_SERVER = "ari"
ARI_TOOLS = ("agendar", "listar_agenda", "cancelar", "recordar_dato", "olvidar_dato",
             "ver_datos", "aprobar_acceso", "revocar_acceso", "ver_accesos",
             "enviar_mensaje", "proponer_codigo")

_READ = {"listar_agenda", "ver_datos"}
_USER_CHAT = _READ | {"agendar", "cancelar", "recordar_dato", "olvidar_dato"}


def allowed_ari_tools(is_owner: bool, context: str) -> tuple[str, ...]:
    """Scheduled tasks and the heartbeat are read-only: nothing autonomous can
    schedule, remember, approve or message (no self-loops, no injected actions)."""
    if context == CHAT:
        allowed = set(ARI_TOOLS) if is_owner else _USER_CHAT
    elif context == TASK:
        allowed = _READ
    elif context == HEARTBEAT:
        allowed = _READ | ({"ver_accesos"} if is_owner else set())
    else:
        allowed = set()
    return tuple(t for t in ARI_TOOLS if t in allowed)
