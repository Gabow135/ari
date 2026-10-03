from dataclasses import dataclass

READ = "read"
ACT = "act"

_ORDER = {READ: 1, ACT: 2}


def level_at_least(have: str, need: str) -> bool:
    """True when the level ``have`` satisfies the requirement ``need`` (read < act)."""
    return _ORDER.get(have, 0) >= _ORDER.get(need, 0)


@dataclass(frozen=True)
class Grant:
    grantor_id: str   # owner of the data
    grantee_id: str   # who received access
    capability: str   # v1: "schedule"
    level: str        # READ | ACT
