import pytest

from ari.application.grants.grant_policy import GrantPolicy
from ari.infrastructure.grants.sqlite_grant_store import SqliteGrantStore
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore


async def _combined_on_revoke(schedule, grants):
    async def on_revoke(user_id: str) -> None:
        await schedule.cancel_user(user_id)
        await grants.forget_user(user_id)
    return on_revoke


@pytest.mark.asyncio
async def test_revoke_clears_grants_both_directions(tmp_path):
    conn = await connect(str(tmp_path / "ari.db"))
    try:
        schedule = SqliteScheduleStore(conn)
        grants = GrantPolicy(SqliteGrantStore(conn))
        await grants.share("A", "B", "schedule", "act")   # B is grantee
        await grants.share("B", "C", "schedule", "read")  # B is grantor
        on_revoke = await _combined_on_revoke(schedule, grants)
        await on_revoke("B")
        assert await grants.allows("B", "A", "schedule", "read") is False
        assert await grants.allows("C", "B", "schedule", "read") is False
    finally:
        await conn.close()
