from ari.domain.grants.entities import Grant, level_at_least


class GrantPolicy:
    """The single decision point for cross-user access, plus a thin facade over
    the grant store for sharing/listing/revoking. Acting on your own data is
    always allowed; otherwise a grant of sufficient level must exist."""

    def __init__(self, store):
        self._store = store

    async def allows(self, grantee_id: str, grantor_id: str, capability: str, min_level: str) -> bool:
        if grantee_id == grantor_id:
            return True
        grant = await self._store.get(grantor_id, grantee_id, capability)
        return grant is not None and level_at_least(grant.level, min_level)

    async def share(self, grantor_id: str, grantee_id: str, capability: str, level: str) -> None:
        await self._store.upsert(grantor_id, grantee_id, capability, level)

    async def revoke(self, grantor_id: str, grantee_id: str, capability: str) -> bool:
        return await self._store.revoke(grantor_id, grantee_id, capability)

    async def given_by(self, user_id: str) -> list[Grant]:
        return await self._store.given_by(user_id)

    async def received_by(self, user_id: str) -> list[Grant]:
        return await self._store.received_by(user_id)

    async def forget_user(self, user_id: str) -> int:
        return await self._store.delete_for_user(user_id)
