from dataclasses import dataclass
from typing import Awaitable, Callable

from ari.application.coding.command_router import (
    parse_coding_command, is_affirmative, is_negative)


@dataclass
class CodingDeps:
    authorizer: object
    pending_store: object
    request_coding: object
    confirm_coding: object
    chat: Callable[[str, str], Awaitable[str]]
    scheduler: Callable


async def route_message(text: str, user_id: str, deps: CodingDeps) -> str | None:
    pending = deps.pending_store.get(user_id)
    if pending is not None:
        if is_affirmative(text):
            deps.scheduler(deps.confirm_coding(user_id))   # background; caller already told to reply
            return "Dale, arranco. Te aviso cuando termine."
        if is_negative(text):
            deps.pending_store.clear(user_id)
            return "Cancelado."
        # fallthrough: not a clear yes/no -> treat as chat
    cmd = parse_coding_command(text)
    if cmd is not None:
        if not deps.authorizer.is_owner(user_id):
            return "El modo código es solo para el dueño; no estás autorizado."
        if deps.pending_store.is_busy(user_id):
            return "Ya tengo un trabajo en curso para vos; esperá a que termine."
        instruction_text, target = cmd
        return await deps.request_coding(user_id, instruction_text, target)
    return await deps.chat(text, user_id)
