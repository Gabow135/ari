from ari.domain.coding.entities import CodingInstruction, CodingPlan, CodingResult


class FakeCoder:
    def __init__(self, plan_summary: str = "do X", fail: bool = False):
        self.plan_summary = plan_summary
        self.fail = fail
        self.planned: list[tuple[CodingInstruction, str]] = []
        self.executed: list[tuple[str, CodingPlan]] = []

    async def plan(self, instruction: CodingInstruction, target_dir: str) -> CodingPlan:
        self.planned.append((instruction, target_dir))
        return CodingPlan(summary=self.plan_summary, target_dir=target_dir,
                          instruction_text=instruction.text)

    async def execute(self, plan: CodingPlan, branch: str) -> CodingResult:
        self.executed.append((branch, plan))
        if self.fail:
            return CodingResult(ok=False, branch=branch, detail="boom")
        return CodingResult(ok=True, branch=branch, changed_files=["f.py"], commits=["deadbee"])
