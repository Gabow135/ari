from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ari.application.coding.command_router import (
    is_affirmative,
    is_dale,
    is_negative,
    parse_coding_command,
)
from ari.application.coding.request_runner import BUSY
from ari.application.text_format import truncate


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
        # A plan proposed in the background (proponer_codigo) only executes on an
        # exact "dale"; any other affirmative (sí, ok…) falls through to chat as
        # if nothing were pending, same as an unrelated reply. /code plans keep
        # the looser _AFFIRMATIVE set they always had.
        confirmed = is_dale(text) if pending.proposed else is_affirmative(text)
        if confirmed:
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
        instruction_text, target = cmd
        if deps.pending_store.is_busy(user_id):
            return "Ya tengo un trabajo en curso para ti; espera a que termine."
        # Claim the planning slot so a background proposal can't plan alongside.
        if not deps.pending_store.mark_planning(user_id):
            return BUSY.format(truncate(instruction_text))
        try:
            return await deps.request_coding(user_id, instruction_text, target)
        finally:
            deps.pending_store.clear_planning(user_id)
    return await deps.chat(text, user_id)
