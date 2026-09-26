import textwrap

import pytest

from ari.config.settings import Settings
from ari.infrastructure.skills.skill_loader import load_skill


def test_load_skill_calls_factory_with_config(tmp_path):
    (tmp_path / "skill.py").write_text(textwrap.dedent("""
        class S:
            def __init__(self, config):
                self.config = config
        def build_skill(config):
            return S(config)
    """))
    s = load_skill("t", str(tmp_path), "skill.py", "build_skill", {"k": 1})
    assert s.config == {"k": 1}


def test_load_skill_propagates_errors(tmp_path):
    (tmp_path / "skill.py").write_text("raise RuntimeError('boom')\n")
    with pytest.raises(RuntimeError):
        load_skill("t", str(tmp_path), "skill.py", "build_skill", {})


def test_settings_default_skills_dir(monkeypatch):
    monkeypatch.setenv("ARI_TELEGRAM_BOT_TOKEN", "x")
    assert Settings().skills_dir == "./skills"
