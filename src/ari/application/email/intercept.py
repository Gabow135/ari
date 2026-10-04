class EmailBlobInterceptor:
    """Turns a pasted ari-mail blob into a stored account + a chat reply, never
    raising into the dispatch loop."""

    def __init__(self, connect_use_case):
        self._connect = connect_use_case

    async def handle(self, user_id: str, text: str) -> str:
        try:
            label = await self._connect(user_id, text)
        except ValueError as exc:
            return f"No pude conectar la casilla: {exc}."
        return f"\U0001f4e7 Casilla «{label}» conectada ✅"
