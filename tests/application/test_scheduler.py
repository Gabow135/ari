import asyncio

from ari.application.schedule.scheduler import Scheduler


async def test_tick_runs_every_job_and_isolates_failures():
    calls = []

    async def ok():
        calls.append("ok")

    async def boom():
        raise RuntimeError("x")

    await Scheduler([boom, ok]).tick()
    assert calls == ["ok"]


async def test_start_loops_until_stopped():
    calls = []

    async def job():
        calls.append(1)

    s = Scheduler([job], interval=0.01)
    s.start()
    await asyncio.sleep(0.05)
    await s.stop()
    n = len(calls)
    await asyncio.sleep(0.03)
    assert n >= 2 and len(calls) == n
