"""In-memory store of short-lived, single-session access tokens for the vault web
maintainer. The clock is injected so expiry is deterministic in tests."""
import secrets
from collections.abc import Callable


class VaultLinkStore:
    def __init__(self, ttl_seconds: int, clock: Callable[[], float]):
        self._ttl = ttl_seconds
        self._clock = clock
        self._expiry: dict[str, float] = {}

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        self._expiry[token] = self._clock() + self._ttl
        return token

    def sweep(self) -> None:
        now = self._clock()
        for token in [t for t, exp in self._expiry.items() if exp <= now]:
            del self._expiry[token]

    def valid(self, token: str) -> bool:
        self.sweep()
        return token in self._expiry

    def active_count(self) -> int:
        self.sweep()
        return len(self._expiry)
