import json
import textwrap

from ari.application.skills.skill_manager import SkillManager
from ari.domain.skills.models import Attachment, RawInbound
from tests.fakes import FakeVault


def _write_skill(root, name, *, enabled=True, required=None, body=None, owner_only=True):
    d = root / name
    d.mkdir()
    (d / "skill.json").write_text(json.dumps({
        "name": name, "version": "0.1.0", "enabled": enabled, "owner_only": owner_only,
        "entrypoint": "skill.py", "factory": "build_skill",
        "required_secrets": required or [], "hooks": ["inbound_transform", "outbound_transform"],
        "config": {"tag": name},
    }))
    (d / "skill.py").write_text(body or textwrap.dedent("""
        from ari.domain.skills.models import Delivery
        class S:
            def __init__(self, config): self.config = config
            async def on_inbound(self, raw, ctx):
                return f"transcript::{ctx.config['tag']}"
            async def on_outbound(self, reply, origin, ctx):
                return [Delivery(kind="voice", data=b"WAV", mime="audio/wav")]
        def build_skill(config): return S(config)
    """))
    return d


def test_active_when_secret_present_and_runs_inbound():
    import pathlib
    import tempfile
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "a", required=["A_KEY"])
    m = SkillManager(str(root), vault=FakeVault({"A_KEY": "v"}), env={})
    statuses = {s.name: s for s in m.list()}
    assert statuses["a"].state == "active"


async def test_run_inbound_returns_transcript():
    import pathlib
    import tempfile
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "a", required=["A_KEY"])
    m = SkillManager(str(root), vault=FakeVault({"A_KEY": "v"}), env={})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))
    assert await m.run_inbound(raw, is_owner=True) == "transcript::a"


def test_missing_secret_is_needs_secrets_and_not_loaded():
    import pathlib
    import tempfile
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "a", required=["A_KEY"])
    m = SkillManager(str(root), vault=FakeVault({}), env={})
    s = m.list()[0]
    assert s.state == "needs_secrets" and s.missing_secrets == ["A_KEY"]


def test_required_secret_names_are_unioned_and_deduped():
    import pathlib
    import tempfile
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "a", required=["A_KEY", "SHARED"])
    _write_skill(root, "b", required=["SHARED", "B_KEY"])
    m = SkillManager(str(root), vault=FakeVault({}), env={})
    assert m.required_secret_names() == ["A_KEY", "B_KEY", "SHARED"]


def test_broken_skill_is_isolated_as_failed():
    import pathlib
    import tempfile
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "ok")
    _write_skill(root, "bad", body="raise RuntimeError('boom')\n")
    m = SkillManager(str(root), vault=FakeVault({}), env={})
    states = {s.name: s.state for s in m.list()}
    assert states["ok"] == "active"
    assert states["bad"] == "failed"


async def test_owner_only_skill_hidden_from_non_owner():
    import pathlib
    import tempfile
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "a", owner_only=True)
    m = SkillManager(str(root), vault=FakeVault({}), env={})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))
    assert await m.run_inbound(raw, is_owner=False) is None
    assert await m.run_inbound(raw, is_owner=True) == "transcript::a"


async def test_secret_scoping_rejects_undeclared():
    import pathlib
    import tempfile
    body = textwrap.dedent("""
        class S:
            def __init__(self, config): pass
            async def on_inbound(self, raw, ctx):
                return ctx.secret("OTHER")   # not declared -> PermissionError
        def build_skill(config): return S(config)
    """)
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "a", required=["A_KEY"], body=body)
    m = SkillManager(str(root), vault=FakeVault({"A_KEY": "v", "OTHER": "z"}), env={})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))
    import pytest
    with pytest.raises(PermissionError):
        await m.run_inbound(raw, is_owner=True)


def test_set_enabled_flips_manifest_and_reloads():
    import pathlib
    import tempfile
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "a", enabled=True)
    m = SkillManager(str(root), vault=FakeVault({}), env={})
    assert m.list()[0].enabled is True
    assert m.set_enabled("a", False) is True
    assert m.list()[0].enabled is False
    assert m.set_enabled("nope", True) is False


def test_reload_picks_up_new_skill():
    import pathlib
    import tempfile
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "alpha")
    m = SkillManager(str(root), vault=FakeVault({}), env={})
    # Add a second skill AFTER construction
    _write_skill(root, "beta")
    names = m.reload()
    assert "beta" in names
    assert "beta" in [s.name for s in m.list()]


def test_reload_returns_only_active_names():
    import pathlib
    import tempfile
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "enabled_skill", enabled=True)
    _write_skill(root, "disabled_skill", enabled=False)
    m = SkillManager(str(root), vault=FakeVault({}), env={})
    names = m.reload()
    assert "enabled_skill" in names
    assert "disabled_skill" not in names


def test_reload_drops_removed_skill():
    import pathlib
    import shutil
    import tempfile
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "to_remove")
    _write_skill(root, "to_keep")
    m = SkillManager(str(root), vault=FakeVault({}), env={})
    shutil.rmtree(str(root / "to_remove"))
    names = m.reload()
    assert "to_remove" not in names
    assert "to_remove" not in [s.name for s in m.list()]
    assert "to_keep" in names
