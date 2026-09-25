from ari.application.coding.command_router import (
    parse_coding_command, is_affirmative, is_negative)


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
