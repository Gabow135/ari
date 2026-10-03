import asyncio


class KeyedLocks:
    """A registry of per-key async locks.

    Operations sharing a key run one at a time; different keys run concurrently.
    Used to serialize a single user's turns (so two of their messages never race
    the same working memory) while keeping different users fully independent.

    The lock set grows with the number of distinct keys seen. For a bot with a
    bounded set of users that is negligible; locks are cheap and never freed, so
    a key's lock identity is stable for the process lifetime.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def __call__(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock
