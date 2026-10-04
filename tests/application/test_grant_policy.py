import pytest

from ari.application.grants.grant_policy import GrantPolicy
from ari.domain.grants.entities import ACT, READ
from ari.infrastructure.grants.sqlite_grant_store import SqliteGrantStore
from ari.infrastructure.persistence.db import connect


@pytest.fixture
async def policy(tmp_path):
    conn = await connect(str(tmp_path / "ari.db"))
    try:
        yield GrantPolicy(SqliteGrantStore(conn))
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_act_grant_satisfies_read_and_act(policy):
    await policy.share("A", "B", "schedule", ACT)
    assert await policy.allows("B", "A", "schedule", READ) is True
    assert await policy.allows("B", "A", "schedule", ACT) is True


@pytest.mark.asyncio
async def test_read_grant_does_not_satisfy_act(policy):
    await policy.share("A", "B", "schedule", READ)
    assert await policy.allows("B", "A", "schedule", READ) is True
    assert await policy.allows("B", "A", "schedule", ACT) is False


@pytest.mark.asyncio
async def test_no_grant_denies(policy):
    assert await policy.allows("B", "A", "schedule", READ) is False


@pytest.mark.asyncio
async def test_acting_on_own_data_always_allowed(policy):
    assert await policy.allows("A", "A", "schedule", ACT) is True


@pytest.mark.asyncio
async def test_revoke_and_forget(policy):
    await policy.share("A", "B", "schedule", ACT)
    await policy.share("C", "B", "schedule", READ)
    assert await policy.revoke("A", "B", "schedule") is True
    assert await policy.forget_user("B") == 1  # only the C->B grant remains
