from ari.application.coding.command_router import (
    is_affirmative,
    is_dale,
    is_negative,
    parse_coding_command,
)


def test_parse_code_command():
    assert parse_coding_command("/code add a healthcheck") == ("add a healthcheck", None)


def test_parse_fase2_command():
    assert parse_coding_command("/fase2 empezá la fase 2") == ("empezá la fase 2", None)


def test_parse_target_prefix():
    assert parse_coding_command("/code dir:proj add X") == ("add X", "proj")


def test_parse_non_command_returns_none():
    assert parse_coding_command("hola, cómo estás") is None
    assert parse_coding_command("/code   ") is None   # empty instruction


def test_affirmative_and_negative():
    assert is_affirmative("dale")
    assert is_affirmative("  Sí ")
    assert not is_affirmative("dale pero esperá")   # only exact affirmatives execute
    assert is_negative("no")
    assert is_negative("cancelar")
    assert not is_negative("nope maybe")


def test_codex_prefix_is_not_a_command():
    """/codex foo must NOT be parsed as a /code command."""
    assert parse_coding_command("/codex foo") is None


def test_coder_prefix_is_not_a_command():
    assert parse_coding_command("/coder do something") is None


def test_code_at_botname_is_parsed():
    """/code@AriBot add X should parse as ("add X", None)."""
    assert parse_coding_command("/code@AriBot add X") == ("add X", None)


def test_fase2_at_botname_is_parsed():
    assert parse_coding_command("/fase2@AriBot fix it") == ("fix it", None)


def test_is_dale_exact_case_insensitive_trimmed():
    assert is_dale("dale")
    assert is_dale("  Dale  ")
    assert is_dale("DALE")


def test_is_dale_strips_trailing_punctuation_and_emoji():
    assert is_dale("dale!")
    assert is_dale("dale.")
    assert is_dale("dale 👍")


def test_is_dale_rejects_other_affirmatives_and_variants():
    assert not is_dale("sí")
    assert not is_dale("ok")
    assert not is_dale("dale total")
    assert not is_dale("dale pero esperá")
