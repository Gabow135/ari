from ari.domain.memory.fact_keys import normalize_key


def test_lowercase():
    assert normalize_key("Ciudad") == "ciudad"


def test_strip_and_collapse_spaces():
    assert normalize_key("  vive   en ") == "vive_en"


def test_uppercase_multi_word():
    assert normalize_key("FAVORITE COLOR") == "favorite_color"


def test_empty_string():
    assert normalize_key("") == ""


def test_single_word_unchanged():
    assert normalize_key("name") == "name"


def test_internal_whitespace_becomes_single_underscore():
    assert normalize_key("a   b   c") == "a_b_c"
