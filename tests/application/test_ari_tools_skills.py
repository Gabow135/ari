# tests/application/test_ari_tools_skills.py
import json
import pathlib
import tempfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.ari_tools import DENIED, Actor, AriTools
from ari.application.skills.skill_manager import SkillManager
from ari.domain.tools.ari_permissions import CHAT, HEARTBEAT, TASK
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)


def _skills_dir():
    root = pathlib.Path(tempfile.mkdtemp())
    for name, enabled, desc in [("groq_audio", True, "Escucha audios"),
                                ("groq_vision", False, "Ve imágenes")]:
        d = root / name
        d.mkdir()
        (d / "skill.json").write_text(json.dumps({
            "name": name, "version": "0.1.0", "enabled": enabled, "owner_only": True,
            "description": desc, "entrypoint": "skill.py", "factory": "build_skill",
            "required_secrets": [], "hooks": ["inbound_transform"], "config": {}}))
        (d / "skill.py").write_text("def build_skill(c): return object()\n")
    return root


@pytest.fixture
async def env():
    conn = await connect(":memory:", embedding_dim=4)
    skills = SkillManager(str(_skills_dir()), load=False)
    yield conn, skills, SqliteTurnLog(conn)
    await conn.close()


def _tools(env, owner=True, context=CHAT):
    conn, skills, log = env
    actor = Actor("42", "42", "Gabriel", owner, context, "t1")
    return AriTools(actor, schedule=SqliteScheduleStore(conn),
                    memory=SqliteMemoryAdapter(conn, embedding_dim=4), turn_log=log, tz=TZ,
                    max_items=20, clock=lambda: NOW, skills=skills)


async def test_ver_skills_lists_state_and_description(env):
    out = await _tools(env).ver_skills()
    assert "groq_audio [on]" in out and "Escucha audios" in out
    assert "groq_vision [off]" in out and "Ve imágenes" in out


async def test_activar_skill_flips_manifest_and_writes_receipt(env):
    _, skills, log = env
    out = await _tools(env).activar_skill("groq_vision")
    assert "groq_vision" in out and "activé" in out.lower()
    assert "activo" not in out.lower()   # must not claim it is running
    assert {c["name"]: c["enabled"] for c in skills.catalog()}["groq_vision"] is True
    assert await log.receipts("t1") == [out]


async def test_desactivar_skill_flips_off(env):
    _, skills, _ = env
    await _tools(env).desactivar_skill("groq_audio")
    assert {c["name"]: c["enabled"] for c in skills.catalog()}["groq_audio"] is False


async def test_case_insensitive_name(env):
    _, skills, _ = env
    await _tools(env).activar_skill("GROQ_VISION")
    assert {c["name"]: c["enabled"] for c in skills.catalog()}["groq_vision"] is True


async def test_unknown_name_lists_and_changes_nothing(env):
    _, skills, _ = env
    out = await _tools(env).activar_skill("banana")
    assert "banana" in out and "groq_audio" in out and "groq_vision" in out
    # nothing toggled
    assert {c["name"]: c["enabled"] for c in skills.catalog()} == {"groq_audio": True, "groq_vision": False}


@pytest.mark.parametrize("owner,context", [(False, CHAT), (True, TASK), (True, HEARTBEAT)])
async def test_denied_outside_owner_chat(env, owner, context):
    _, skills, _ = env
    assert await _tools(env, owner, context).activar_skill("groq_vision") == DENIED
    assert await _tools(env, owner, context).ver_skills() == DENIED
    assert {c["name"]: c["enabled"] for c in skills.catalog()}["groq_vision"] is False


async def test_write_time_miss_returns_no_encontre_no_receipt(env):
    """When set_enabled returns False (TOCTOU: manifest vanished at write time),
    return 'No encontré' message without success receipt."""
    conn, _, log = env
    actor = Actor("42", "42", "Gabriel", True, CHAT, "t1")

    # Create a stub skills manager that passes catalog() but fails at write time
    class FakeSkillsForTOCTOU:
        def catalog(self):
            return [{"name": "groq_audio", "enabled": True, "description": "Audio",
                    "required_secrets": []}]

        def set_enabled(self, name: str, enabled: bool) -> bool:
            # Simulate manifest vanishing between catalog() and write
            return False

    tools = AriTools(actor, schedule=SqliteScheduleStore(conn),
                     memory=SqliteMemoryAdapter(conn, embedding_dim=4), turn_log=log, tz=TZ,
                     max_items=20, clock=lambda: NOW, skills=FakeSkillsForTOCTOU())

    out = await tools.activar_skill("groq_audio")
    # Must contain "No encontré" and the skill name
    assert "No encontré" in out
    assert "groq_audio" in out
    # Must NOT contain success indicator (emoji or past-tense verb)
    assert "🧩" not in out and "Activé" not in out
    # Must NOT have a receipt logged
    assert await log.receipts("t1") == []
