import logging

log = logging.getLogger("ari.email")


class EmailEnrollRunner:
    """Claims enroll requests and sends each user the offline enroll HTML as a
    Telegram document (the MCP server can't send; the bot can)."""

    def __init__(self, requests, sealed_box, render, send_document, send):
        self._requests, self._box = requests, sealed_box
        self._render, self._send_document = render, send_document
        self._send = send

    async def __call__(self) -> None:
        for req in await self._requests.claim_pending():
            try:
                html = self._render(self._box.public_key_b64())
                await self._send_document(req.chat_id, "conectar-correo.html",
                                          html.encode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.warning("enroll send failed for %s: %s", req.chat_id, exc)
                await self._send(req.chat_id,
                                 "No pude preparar el archivo para conectar tu correo; "
                                 "avisa a tu creador.")
            await self._requests.finish(req.id)
