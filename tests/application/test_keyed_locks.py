import asyncio

from ari.application.concurrency.keyed_locks import KeyedLocks


async def test_same_key_returns_same_lock():
    locks = KeyedLocks()
    assert locks("x") is locks("x")
    assert locks("x") is not locks("y")


async def test_same_key_serializes():
    locks = KeyedLocks()
    order: list[tuple[str, int]] = []

    async def worker(n: int, hold: float) -> None:
        async with locks("user-1"):
            order.append(("start", n))
            await asyncio.sleep(hold)
            order.append(("end", n))

    # Worker 1 grabs the lock first and holds it; worker 2 must wait for it to
    # finish, so starts/ends never interleave.
    await asyncio.gather(worker(1, 0.05), worker(2, 0.0))
    assert order == [("start", 1), ("end", 1), ("start", 2), ("end", 2)]


async def test_different_keys_do_not_block_each_other():
    locks = KeyedLocks()
    a_inside = asyncio.Event()
    b_inside = asyncio.Event()

    async def worker_a() -> None:
        async with locks("a"):
            a_inside.set()
            await asyncio.wait_for(b_inside.wait(), timeout=1)

    async def worker_b() -> None:
        async with locks("b"):
            b_inside.set()
            await asyncio.wait_for(a_inside.wait(), timeout=1)

    # Both must be inside their own lock at the same time. If distinct keys
    # shared one lock this would deadlock and time out.
    await asyncio.gather(worker_a(), worker_b())
