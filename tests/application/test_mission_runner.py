import asyncio

import pytest

from ari.application.concurrency.agent_pool import AgentPool
from ari.application.missions.run_pending_missions import MissionRunner
from ari.domain.ports.gateway_port import OutgoingMessage
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_missions import SqliteMissions


@pytest.fixture
async def missions():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteMissions(conn)
    await conn.close()


async def test_pool_serializes_missions(missions):
    pool = AgentPool(1)
    current = peak = 0
    release = asyncio.Event()

    async def handler(incoming, allow_actions=True):
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        await release.wait()
        current -= 1
        return OutgoingMessage(incoming.chat_id, "ok")

    sent, tasks = [], []

    async def send(chat_id, text):
        sent.append((chat_id, text))

    def spawn(coro):
        tasks.append(asyncio.ensure_future(coro))

    await missions.add("1", "1", "do a")
    await missions.add("2", "2", "do b")
    runner = MissionRunner(missions, handler, send, spawn, pool=pool)
    await runner()
    await asyncio.sleep(0.05)
    assert peak == 1  # pool size 1: only one mission's heavy work runs at a time
    release.set()
    await asyncio.gather(*tasks)
    assert peak == 1
