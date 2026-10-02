"""Tests for importance+decay Settings knobs (T4 — Frente 3)."""
import pytest

from ari.config.settings import Settings


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    return Settings(_env_file=None)


def test_reinforce_delta_default(settings):
    assert settings.reinforce_delta == pytest.approx(0.1)


def test_decay_factor_default(settings):
    assert settings.decay_factor == pytest.approx(0.9)


def test_prune_floor_default(settings):
    assert settings.prune_floor == pytest.approx(0.15)


def test_prune_min_age_days_default(settings):
    assert settings.prune_min_age_days == 7


def test_consolidate_interval_hours_default(settings):
    assert settings.consolidate_interval_hours == 6
