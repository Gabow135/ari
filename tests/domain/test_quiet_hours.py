from datetime import datetime

import pytest

from ari.domain.schedule.quiet_hours import is_quiet, parse_window


def test_parse_window():
    assert parse_window("22-7") == (22, 7)
    assert parse_window(" 9-17 ") == (9, 17)
    assert parse_window("") is None


@pytest.mark.parametrize("spec", ["25-7", "x-7", "22"])
def test_parse_window_rejects_garbage(spec):
    with pytest.raises(ValueError):
        parse_window(spec)


@pytest.mark.parametrize("hour,quiet", [(21, False), (22, True), (23, True), (0, True),
                                        (6, True), (7, False), (12, False)])
def test_window_crossing_midnight(hour, quiet):
    assert is_quiet(datetime(2026, 9, 25, hour, 30), (22, 7)) is quiet


def test_daytime_window_and_none():
    assert is_quiet(datetime(2026, 9, 25, 10), (9, 17))
    assert not is_quiet(datetime(2026, 9, 25, 17), (9, 17))
    assert not is_quiet(datetime(2026, 9, 25, 3), None)
    assert not is_quiet(datetime(2026, 9, 25, 3), (5, 5))
