from ari.domain.grants.entities import ACT, READ, Grant, level_at_least


def test_level_ordering():
    assert level_at_least(ACT, READ) is True
    assert level_at_least(ACT, ACT) is True
    assert level_at_least(READ, READ) is True
    assert level_at_least(READ, ACT) is False


def test_level_unknown_is_lowest():
    assert level_at_least("", READ) is False
    assert level_at_least(READ, "") is True


def test_grant_is_frozen():
    g = Grant("1", "2", "schedule", ACT)
    assert (g.grantor_id, g.grantee_id, g.capability, g.level) == ("1", "2", "schedule", ACT)
