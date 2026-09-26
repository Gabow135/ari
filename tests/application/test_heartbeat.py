from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.schedule.heartbeat import Heartbeat
from ari.domain.agent.agent_service import AgentService
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from tests.fakes import FakeLLM, FakeMemory

TZ = ZoneInfo("America/Guayaquil")
DAY = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)  # 10:00 local


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteScheduleStore(conn)
    await conn.close()


def _hb(store, clock, reply="💬 Tienes la reunión a las 11, ¿preparo algo?", minutes=60):
    sent = []

    async def send(chat_id, text):
        sent.append((chat_id, text))

    llm, mem = FakeLLM(reply=reply), FakeMemory()
    hb = Heartbeat(llm=llm, memory=mem, store=store, agent=AgentService(),
                   soul=lambda: "Soy Ari", checklist=lambda: "- Revisa pendientes",
                   owners={"42"}, send=send, tz=TZ, quiet=(22, 7),
                   interval_minutes=minutes, clock=clock)
    return hb, sent, llm, mem


async def test_first_beat_waits_one_interval(store):
    clock = Clock(DAY)
    hb, sent, llm, _ = _hb(store, clock)
    await hb()
    assert llm.calls == []
    clock.now += timedelta(minutes=59)
    await hb()
    assert llm.calls == []
    clock.now += timedelta(minutes=2)
    await hb()
    assert len(llm.calls) == 1 and sent[0][0] == "42" and sent[0][1].startswith("💡 ")


async def test_prompt_contains_checklist_and_nada_rule(store):
    clock = Clock(DAY)
    hb, _, llm, _ = _hb(store, clock)
    await hb()
    clock.now += timedelta(hours=1)
    await hb()
    system = llm.calls[0][0]
    assert "Soy Ari" in system and "- Revisa pendientes" in system and "NADA" in system


async def test_nada_sends_nothing(store):
    clock = Clock(DAY)
    hb, sent, llm, mem = _hb(store, clock, reply="NADA.")
    await hb()
    clock.now += timedelta(hours=1)
    await hb()
    assert len(llm.calls) == 1 and sent == []


async def test_daily_cap_and_history(store):
    clock = Clock(DAY)
    hb, sent, _, mem = _hb(store, clock)
    await hb()
    for _ in range(5):
        clock.now += timedelta(hours=1)
        await hb()
    assert len(sent) == 3
    stored = await mem.recent_messages("42", 10)
    assert [m.role for m in stored] == ["assistant"] * 3


async def test_quiet_hours_and_disabled(store):
    clock = Clock(datetime(2026, 9, 26, 4, 0, tzinfo=timezone.utc))  # 23:00 local
    hb, _, llm, _ = _hb(store, clock)
    await hb()
    clock.now += timedelta(hours=2)
    await hb()
    assert llm.calls == []
    off, _, llm_off, _ = _hb(store, Clock(DAY), minutes=0)
    await off()
    assert llm_off.calls == []


async def test_action_blocks_in_heartbeat_are_ignored(store):
    clock = Clock(DAY)
    hb, sent, _, _ = _hb(store, clock, reply='Ojo <ari-action>{"type":"cancel","id":1}'
                                               '</ari-action>')
    await hb()
    clock.now += timedelta(hours=1)
    await hb()
    assert sent == [("42", "💡 Ojo")]


from contextlib import asynccontextmanager

from ari.domain.tools.toolset import Toolset, ToolsView, Turn


class _OwnerPolicy:
    def __init__(self):
        self.contexts = []

    @asynccontextmanager
    async def turn(self, user_id, context="chat", actor_name="", chat_id=None):
        self.contexts.append(context)
        yield Turn(Toolset(("WebSearch",), ("WebSearch", "mcp__google"), "/cfg/owner.json"),
                   ToolsView("## Tus herramientas y conexiones\n- 🔌 google: Gmail", True, True),
                   "turn-hb")


async def test_heartbeat_uses_owner_turn_in_heartbeat_context(store):
    clock = Clock(DAY)
    sent = []

    async def send(chat_id, text):
        sent.append((chat_id, text))

    llm, mem, policy = FakeLLM(reply="NADA"), FakeMemory(), _OwnerPolicy()
    hb = Heartbeat(llm=llm, memory=mem, store=store, agent=AgentService(),
                   soul=lambda: "Soy Ari", checklist=lambda: "- revisa correos",
                   owners={"42"}, send=send, tz=TZ, quiet=(22, 7), interval_minutes=60,
                   clock=clock, tools=policy)
    await hb()
    clock.now += timedelta(hours=1)
    await hb()
    assert policy.contexts == ["heartbeat"]
    assert llm.toolsets[0].mcp_config_path == "/cfg/owner.json"
    assert "🔌 google: Gmail" in llm.calls[0][0]
