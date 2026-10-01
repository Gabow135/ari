import json
import pathlib
import tempfile
import textwrap

import pytest

from ari.application.skills.skill_manager import SkillManager
from ari.domain.skills.models import Attachment, RawInbound
from tests.fakes import FakeVault

_READ_OPTIONAL = textwrap.dedent("""
    class S:
        def __init__(self, config): pass
        async def on_inbound(self, raw, ctx):
            return f"opt={ctx.optional_secret('HF')!r}"
    def build_skill(config): return S(config)
""")


def _write(root, name, *, required=None, optional=None, body=None):
    d = root / name
    d.mkdir()
    (d / "skill.json").write_text(json.dumps({
        "name": name, "version": "0.1.0", "enabled": True, "owner_only": True,
        "entrypoint": "skill.py", "factory": "build_skill",
        "required_secrets": required or [], "optional_secrets": optional or [],
        "hooks": ["inbound_transform"], "config": {},
    }))
    (d / "skill.py").write_text(body or _READ_OPTIONAL)
    return d


def _raw():
    return RawInbound(user_id="1", chat_id="2",
                      attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))


def test_optional_secret_missing_does_not_block_activation():
    root = pathlib.Path(tempfile.mkdtemp())
    _write(root, "a", optional=["HF"])
    m = SkillManager(str(root), vault=FakeVault({}), env={})
    s = m.list()[0]
    assert s.state == "active" and s.missing_secrets == []


async def test_optional_secret_returns_value_when_present():
    root = pathlib.Path(tempfile.mkdtemp())
    _write(root, "a", optional=["HF"])
    m = SkillManager(str(root), vault=FakeVault({"HF": "tok"}), env={})
    assert await m.run_inbound(_raw(), is_owner=True) == "opt='tok'"


async def test_optional_secret_returns_none_when_absent():
    root = pathlib.Path(tempfile.mkdtemp())
    _write(root, "a", optional=["HF"])
    m = SkillManager(str(root), vault=FakeVault({}), env={})
    assert await m.run_inbound(_raw(), is_owner=True) == "opt=None"


async def test_optional_secret_undeclared_raises():
    body = textwrap.dedent("""
        class S:
            def __init__(self, config): pass
            async def on_inbound(self, raw, ctx):
                return ctx.optional_secret("OTHER")   # not declared -> PermissionError
        def build_skill(config): return S(config)
    """)
    root = pathlib.Path(tempfile.mkdtemp())
    _write(root, "a", optional=["HF"], body=body)
    m = SkillManager(str(root), vault=FakeVault({"OTHER": "z"}), env={})
    with pytest.raises(PermissionError):
        await m.run_inbound(_raw(), is_owner=True)
