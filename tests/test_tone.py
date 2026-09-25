"""Ari speaks neutral Spanish with tuteo: guard user-facing text against voseo."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Only voseo-specific forms (stressed final vowel / enclitic without accent);
# their tuteo twins (responde, manda, espera, escríbeme, pásaselo…) are fine.
_VOSEO = re.compile(
    r"(?i)\b(tenés|podés|querés|necesitás|sabés|respondé|mandá|mandame|escribime|"
    r"esperá|pasáselo|aprobá|volvé|decime|avisame|para vos)\b")


def test_no_voseo_in_user_facing_text():
    files = [*ROOT.joinpath("src").rglob("*.py"), *ROOT.joinpath("soul").rglob("*.md")]
    hits = [f"{p.relative_to(ROOT)}:{n}: {line.strip()}"
            for p in files
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if _VOSEO.search(line)]
    assert hits == []


def test_guard_catches_voseo_and_allows_tuteo():
    assert _VOSEO.search("Respondé «dale» y esperá")
    assert not _VOSEO.search("Responde «dale» y espera; escríbeme, pásaselo")
