"""Single-use, short-lived token->filepath store for the LAN file server.
A token is consumed (removed) on claim, so a leaked link works at most once."""
import secrets
from collections.abc import Callable


class FileLinkStore:
    def __init__(self, ttl_seconds: int, clock: Callable[[], float]):
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[str, tuple[str, float]] = {}  # token -> (path, expiry)

    def create(self, path: str) -> str:
        token = secrets.token_urlsafe(32)
        self._entries[token] = (path, self._clock() + self._ttl)
        return token

    def _sweep(self) -> None:
        now = self._clock()
        for t in [t for t, (_p, exp) in self._entries.items() if exp <= now]:
            del self._entries[t]

    def claim(self, token: str) -> str | None:
        self._sweep()
        entry = self._entries.pop(token, None)  # single-use: remove on claim
        return entry[0] if entry is not None else None

    def active_count(self) -> int:
        self._sweep()
        return len(self._entries)
