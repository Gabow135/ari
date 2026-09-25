import pytest

from ari.domain.coding.entities import (
    CodingInstruction, CodingPlan, CodingResult, PendingAction)


def test_instruction_rejects_empty_text():
    with pytest.raises(ValueError):
        CodingInstruction(user_id="u1", text="   ")


def test_entities_hold_fields():
    instr = CodingInstruction("u1", "add a healthcheck", target=None)
    plan = CodingPlan(summary="1. add endpoint", target_dir="/repo", instruction_text=instr.text)
    res = CodingResult(ok=True, branch="ari/tg-x", changed_files=["a.py"], commits=["abc123"])
    pend = PendingAction(instruction=instr, plan=plan)
    assert pend.plan.target_dir == "/repo"
    assert res.ok and res.changed_files == ["a.py"]
    assert instr.target is None
