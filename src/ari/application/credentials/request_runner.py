import logging
from datetime import timedelta

from ari.application.text_format import truncate
from ari.domain.credentials.requests import DONE, FAILED, SKIPPED

log = logging.getLogger("ari.credentials")


class CredentialRequestRunner:
    """Claims queued credential requests and answers each with a fresh vault_web link."""

    def __init__(self, requests, vault_web, send, clock, max_age: timedelta = timedelta(hours=1)):
        self._requests, self._vault_web = requests, vault_web
        self._send, self._clock, self._max_age = send, clock, max_age

    async def __call__(self) -> None:
        for req in await self._requests.claim_pending():
            if self._clock() - req.created_at > self._max_age:
                await self._requests.finish(req.id, SKIPPED, "stale")
                continue
            try:
                link = self._vault_web.new_link()
            except Exception as exc:
                log.warning("credential link mint failed: %s", exc)
                await self._requests.finish(req.id, FAILED, str(exc))
                await self._send(req.chat_id, "No pude generar el link para cargar la credencial.")
                continue
            await self._send(req.chat_id,
                f"Para cargar {truncate(req.requested)} abrí este link en la misma red "
                f"(vence pronto):\n{link}\nEl formulario muestra todas las credenciales que "
                f"faltan; completá la que corresponda.")
            await self._requests.finish(req.id, DONE)
