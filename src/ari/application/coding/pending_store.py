from ari.domain.coding.entities import PendingAction


class PendingStore:
    def __init__(self) -> None:
        self._pending: dict[str, PendingAction] = {}
        self._busy: set[str] = set()

    def put(self, user_id: str, action: PendingAction) -> None:
        self._pending[user_id] = action

    def get(self, user_id: str) -> PendingAction | None:
        return self._pending.get(user_id)

    def pop(self, user_id: str) -> PendingAction | None:
        return self._pending.pop(user_id, None)

    def clear(self, user_id: str) -> None:
        self._pending.pop(user_id, None)

    def mark_busy(self, user_id: str) -> None:
        self._busy.add(user_id)

    def clear_busy(self, user_id: str) -> None:
        self._busy.discard(user_id)

    def is_busy(self, user_id: str) -> bool:
        return user_id in self._busy
