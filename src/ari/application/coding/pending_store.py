from ari.domain.coding.entities import PendingAction


class PendingStore:
    def __init__(self) -> None:
        self._pending: dict[str, PendingAction] = {}
        self._busy: set[str] = set()
        self._planning: set[str] = set()

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

    def mark_planning(self, user_id: str) -> bool:
        """Claim the "preparing a plan" slot for user_id. Returns False (and
        does nothing) if it was already claimed — atomic check-and-set since
        there's no await between the check and the add."""
        if user_id in self._planning:
            return False
        self._planning.add(user_id)
        return True

    def clear_planning(self, user_id: str) -> None:
        self._planning.discard(user_id)

    def is_planning(self, user_id: str) -> bool:
        return user_id in self._planning
