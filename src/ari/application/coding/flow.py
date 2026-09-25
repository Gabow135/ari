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
            # Guard is SYNCHRONOUS: pop + mark_busy before scheduling so two
            # rapid "dale" messages cannot both see a pending action.
            if deps.pending_store.is_busy(user_id):
                return "Ya hay un trabajo en curso para ti; espera a que termine."
            action = deps.pending_store.pop(user_id)
            if action is None:
                # Raced with another confirm that already popped it.
                return "Ya hay un trabajo en curso para ti; espera a que termine."
            deps.pending_store.mark_busy(user_id)
            deps.scheduler(deps.confirm_coding(user_id, action))
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
            return "Ya tengo un trabajo en curso para ti; espera a que termine."
        instruction_text, target = cmd
        return await deps.request_coding(user_id, instruction_text, target)
    return await deps.chat(text, user_id)
