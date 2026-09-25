from ari.domain.coding.entities import CodingInstruction, CodingPlan
from tests.coding_fakes import FakeCoder


async def test_fake_coder_plans_and_executes():
    c = FakeCoder(plan_summary="add endpoint")
    instr = CodingInstruction("u1", "add healthcheck")
    plan = await c.plan(instr, "/repo")
    assert plan.summary == "add endpoint"
    assert plan.target_dir == "/repo"
    res = await c.execute(plan, "ari/tg-1")
    assert res.ok and res.branch == "ari/tg-1"
    assert c.executed == [("ari/tg-1", plan)]
