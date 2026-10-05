import logging
import os
from datetime import timedelta

from ari.domain.files.requests import DONE, FAILED, SKIPPED

log = logging.getLogger("ari.files")


class FileServeRequestRunner:
    """Claims queued file requests and answers each with a one-time LAN file link."""

    def __init__(self, requests, vault_web, send, clock, max_age: timedelta = timedelta(hours=1)):
        self._requests, self._vault_web = requests, vault_web
        self._send, self._clock, self._max_age = send, clock, max_age

    async def __call__(self) -> None:
        for req in await self._requests.claim_pending():
            if self._clock() - req.created_at > self._max_age:
                await self._requests.finish(req.id, SKIPPED, "stale")
                continue
            try:
                link = self._vault_web.new_file_link(req.path)
            except Exception as exc:  # noqa: BLE001 — denied/missing/mint failure
                log.warning("file link mint failed: %s", exc)
                await self._requests.finish(req.id, FAILED, str(exc))
                await self._send(req.chat_id, "No pude generar el link para abrir ese archivo.")
                continue
            name = os.path.basename(req.path)
            await self._send(
                req.chat_id,
                f"Abrí «{name}» en la misma red (vence pronto, un solo uso):\n{link}\n"
                "El navegador va a advertir por el certificado; aceptá una vez.")
            await self._requests.finish(req.id, DONE)
