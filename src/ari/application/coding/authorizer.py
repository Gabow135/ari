class Authorizer:
    def __init__(self, owner_ids: set[str]):
        self._owner_ids = {str(x) for x in owner_ids}

    def is_owner(self, user_id: str) -> bool:
        return str(user_id) in self._owner_ids
