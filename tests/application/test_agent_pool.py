import asyncio

import pytest

from ari.application.concurrency.agent_pool import AgentPool


def test_exposes_size():
    assert AgentPool(3).size == 3


def test_rejects_invalid_size():
    with pytest.raises(ValueError):
        AgentPool(0)


async def test_caps_concurrency_to_size():
    pool = AgentPool(2)
    current = 0
    peak = 0

    async def worker() -> None:
        nonlocal current, peak
        async with pool:
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.02)
            current -= 1

    await asyncio.gather(*(worker() for _ in range(6)))
    assert peak == 2


async def test_size_one_serializes():
    pool = AgentPool(1)
    order: list[tuple[str, int]] = []

    async def worker(n: int, hold: float) -> None:
        async with pool:
            order.append(("start", n))
            await asyncio.sleep(hold)
            order.append(("end", n))

    await asyncio.gather(worker(1, 0.03), worker(2, 0.0))
    assert order == [("start", 1), ("end", 1), ("start", 2), ("end", 2)]
