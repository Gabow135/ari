import os

from ari.infrastructure.soul.soul_loader import SoulLoader


def test_missing_soul_returns_none(tmp_path):
    assert SoulLoader(str(tmp_path))() is None


def test_reads_soul_and_reloads_when_changed(tmp_path):
    soul = tmp_path / "SOUL.md"
    soul.write_text("Soy Ari v1", encoding="utf-8")
    load = SoulLoader(str(tmp_path))
    assert load() == "Soy Ari v1"
    soul.write_text("Soy Ari v2 — más larga", encoding="utf-8")
    st = soul.stat()
    os.utime(soul, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert load() == "Soy Ari v2 — más larga"


def test_project_soul_exists_and_is_spanish():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    text = SoulLoader(os.path.join(root, "soul"))()
    assert text and "Ari" in text and "MCP" in text
