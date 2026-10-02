"""Tests for recall-precision Settings knobs (T6)."""
import pytest

from ari.config.settings import Settings


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    return Settings(_env_file=None)


def test_candidate_multiplier_default(settings):
    assert settings.candidate_multiplier == 4


def test_dedup_similarity_default(settings):
    assert settings.dedup_similarity == pytest.approx(0.98)


def test_rank_min_similarity_default(settings):
    assert settings.rank_min_similarity == pytest.approx(0.3)


def test_rank_similarity_weight_default(settings):
    assert settings.rank_similarity_weight == pytest.approx(0.6)


def test_rank_recency_weight_default(settings):
    assert settings.rank_recency_weight == pytest.approx(0.25)


def test_rank_importance_weight_default(settings):
    assert settings.rank_importance_weight == pytest.approx(0.15)


def test_rank_recency_half_life_days_default(settings):
    assert settings.rank_recency_half_life_days == pytest.approx(30.0)


def test_candidate_multiplier_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("ARI_CANDIDATE_MULTIPLIER", "8")
    s = Settings(_env_file=None)
    assert s.candidate_multiplier == 8


def test_dedup_similarity_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("ARI_DEDUP_SIMILARITY", "0.95")
    s = Settings(_env_file=None)
    assert s.dedup_similarity == pytest.approx(0.95)
