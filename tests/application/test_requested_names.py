import pytest

from ari.application.credentials.requested_names import normalize_secret_names


def test_single_valid_name():
    assert normalize_secret_names("NOTION_API_KEY") == ["NOTION_API_KEY"]


def test_lowercase_uppercased():
    assert normalize_secret_names("notion_api_key") == ["NOTION_API_KEY"]


def test_prose_without_underscore_tokens_returns_empty():
    assert normalize_secret_names("el token de Notion") == []


def test_mixed_prose_and_valid():
    assert normalize_secret_names("quiero pasarte NOTION_API_KEY, github_token") == [
        "NOTION_API_KEY",
        "GITHUB_TOKEN",
    ]


def test_ari_fs_root_dropped():
    assert normalize_secret_names("ARI_FS_ROOT") == []


def test_dedup_keeps_first_order():
    result = normalize_secret_names("NOTION_API_KEY GITHUB_TOKEN NOTION_API_KEY")
    assert result == ["NOTION_API_KEY", "GITHUB_TOKEN"]


def test_single_word_no_underscore_returns_empty():
    assert normalize_secret_names("NOTION") == []


def test_empty_string_returns_empty():
    assert normalize_secret_names("") == []


def test_none_returns_empty():
    assert normalize_secret_names(None) == []
