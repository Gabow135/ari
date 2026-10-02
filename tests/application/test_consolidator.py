"""Tests for MemoryConsolidator (T3 — Frente 3 importance + decay)."""
from datetime import UTC, datetime, timedelta

import pytest

from ari.application.memory.consolidator import MemoryConsolidator
from tests.fakes import FakeMemory


class FakeKV:
    """Simple in-memory KV store with kv_get / kv_set interface."""

    def __init__(self):
        self._store: dict[str, str] = {}

    async def kv_get(self, key: str) -> str | None:
        return self._store.get(key)

    async def kv_set(self, key: str, value: str) -> None:
        self._store[key] = value


def _make(mem, kv, clock, *, interval_hours=6, decay_factor=0.9,
          prune_floor=0.15, prune_min_age_days=7):
    return MemoryConsolidator(
        memory=mem, kv=kv, clock=clock,
        interval_hours=interval_hours,
        decay_factor=decay_factor,
        prune_floor=prune_floor,
        prune_min_age_days=prune_min_age_days,
    )


async def test_first_call_runs_decay_and_prune_and_sets_last():
    """First call (no last-run key) must run decay+prune and persist the timestamp."""
    mem = FakeMemory()
    kv = FakeKV()
    now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    consolidator = _make(mem, kv, clock=lambda: now)

    # Spy: track method calls on FakeMemory via subclass
    decay_calls = []
    prune_calls = []
    original_decay = mem.decay_recalls
    original_prune = mem.prune_recalls

    async def spy_decay(factor):
        decay_calls.append(factor)
        return await original_decay(factor)

    async def spy_prune(floor, older_than):
        prune_calls.append((floor, older_than))
        return await original_prune(floor, older_than)

    mem.decay_recalls = spy_decay
    mem.prune_recalls = spy_prune

    await consolidator()

    assert len(decay_calls) == 1
    assert len(prune_calls) == 1
    last = await kv.kv_get("consolidator.last_at")
    assert last == now.isoformat()


async def test_second_call_within_interval_is_skipped():
    """A second call before the interval elapses must not run decay or prune."""
    mem = FakeMemory()
    kv = FakeKV()
    base = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)

    decay_calls = []
    original_decay = mem.decay_recalls

    async def spy_decay(factor):
        decay_calls.append(factor)
        return await original_decay(factor)

    mem.decay_recalls = spy_decay

    # First call: runs and sets last.
    consolidator = _make(mem, kv, clock=lambda: base, interval_hours=6)
    await consolidator()
    assert len(decay_calls) == 1

    # Second call: still within interval (only 1 hour later).
    later = base + timedelta(hours=1)
    consolidator2 = _make(mem, kv, clock=lambda: later, interval_hours=6)
    await consolidator2()
    assert len(decay_calls) == 1  # no additional decay ran


async def test_call_after_interval_elapses_runs_again():
    """A call after the interval has elapsed must run decay+prune again."""
    mem = FakeMemory()
    kv = FakeKV()
    base = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)

    decay_calls = []
    original_decay = mem.decay_recalls

    async def spy_decay(factor):
        decay_calls.append(factor)
        return await original_decay(factor)

    mem.decay_recalls = spy_decay

    # First call at base.
    await _make(mem, kv, clock=lambda: base, interval_hours=6)()
    assert len(decay_calls) == 1

    # Second call at base + 7 hours (past 6-hour interval).
    past_interval = base + timedelta(hours=7)
    await _make(mem, kv, clock=lambda: past_interval, interval_hours=6)()
    assert len(decay_calls) == 2


async def test_memory_exception_does_not_propagate():
    """A crash in decay_recalls must be swallowed; the consolidator must not raise."""

    class ExplodingMemory(FakeMemory):
        async def decay_recalls(self, factor):
            raise RuntimeError("storage exploded")

    mem = ExplodingMemory()
    kv = FakeKV()
    now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)

    consolidator = _make(mem, kv, clock=lambda: now)
    # Must not raise.
    await consolidator()


async def test_prune_uses_configured_params():
    """The floor and prune_min_age_days must be forwarded to prune_recalls correctly."""
    mem = FakeMemory()
    kv = FakeKV()
    now = datetime(2025, 6, 10, 0, 0, 0, tzinfo=UTC)

    prune_calls = []
    original_prune = mem.prune_recalls

    async def spy_prune(floor, older_than):
        prune_calls.append((floor, older_than))
        return await original_prune(floor, older_than)

    mem.prune_recalls = spy_prune

    consolidator = _make(mem, kv, clock=lambda: now,
                         prune_floor=0.2, prune_min_age_days=14)
    await consolidator()

    assert len(prune_calls) == 1
    floor, older_than = prune_calls[0]
    assert floor == pytest.approx(0.2)
    # older_than must be 14 days before now
    expected_cutoff = (now - timedelta(days=14)).isoformat()
    assert older_than == expected_cutoff
