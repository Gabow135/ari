import json
import pathlib
import tempfile

from ari.application.skills.skill_manager import SkillManager
from tests.fakes import FakeVault


def _skill(root, name, *, enabled, description="", required=None, body="def build_skill(c): return object()\n"):
    d = root / name
    d.mkdir()
    (d / "skill.json").write_text(json.dumps({
        "name": name, "version": "0.1.0", "enabled": enabled, "owner_only": True,
        "description": description, "entrypoint": "skill.py", "factory": "build_skill",
        "required_secrets": required or [], "hooks": ["inbound_transform"], "config": {},
    }))
    (d / "skill.py").write_text(body)
    return d


def _root():
    return pathlib.Path(tempfile.mkdtemp())


def test_load_false_does_not_import_skill_code():
    root = _root()
    # This skill.py raises on import — with load=False it must NOT be imported.
    _skill(root, "boom", enabled=True, body="raise RuntimeError('must not import')\n")
    m = SkillManager(str(root), vault=FakeVault({}), env={}, load=False)
    statuses = {s.name: s for s in m.list()}
    assert "boom" in statuses          # discovered
    assert statuses["boom"].state == "active"   # enabled + no secrets, but instance not loaded
    # No exception raised means the loader was never called.


def test_catalog_reads_manifest_without_resolving_secrets():
    root = _root()
    _skill(root, "groq_vision", enabled=False, description="Ve imágenes",
           required=["GROQ_API_KEY"])
    _skill(root, "documents", enabled=True, description="Lee documentos")
    m = SkillManager(str(root), vault=None, env={}, load=False)
    cat = {c["name"]: c for c in m.catalog()}
    assert cat["groq_vision"]["enabled"] is False
    assert cat["groq_vision"]["description"] == "Ve imágenes"
    assert cat["groq_vision"]["required_secrets"] == ["GROQ_API_KEY"]
    assert cat["documents"]["enabled"] is True and cat["documents"]["required_secrets"] == []


def test_set_enabled_still_flips_under_load_false():
    root = _root()
    _skill(root, "documents", enabled=False, description="d")
    m = SkillManager(str(root), vault=None, env={}, load=False)
    assert m.set_enabled("documents", True) is True
    assert {c["name"]: c["enabled"] for c in m.catalog()}["documents"] is True
    assert m.set_enabled("nope", True) is False
