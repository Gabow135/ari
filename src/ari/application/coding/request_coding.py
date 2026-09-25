import logging

from ari.domain.coding.coder_port import CoderPort
from ari.domain.coding.entities import CodingInstruction, PendingAction

log = logging.getLogger("ari.request_coding")


class RequestCoding:
    def __init__(self, coder: CoderPort, workspace, pending_store, default_dir: str):
        self._coder = coder
        self._ws = workspace
        self._store = pending_store
        self._default_dir = default_dir

    async def __call__(self, user_id: str, instruction_text: str, target: str | None) -> str:
        try:
            target_dir = self._ws.resolve(target, self._default_dir)
        except ValueError as exc:
            log.info("coding target refused: %s", exc)
            return "No puedo trabajar ahí: el destino queda fuera de la carpeta permitida."
        instruction = CodingInstruction(user_id, instruction_text, target)
        plan = await self._coder.plan(instruction, target_dir)
        self._store.put(user_id, PendingAction(instruction, plan))
        return (f"Plan para `{target_dir}`:\n{plan.summary}\n\n"
                "Respondé *dale* para ejecutar, o *no* para cancelar.")
