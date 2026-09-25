from typing import Protocol

from ari.domain.coding.entities import CodingInstruction, CodingPlan, CodingResult


class CoderPort(Protocol):
    async def plan(self, instruction: CodingInstruction, target_dir: str) -> CodingPlan: ...
    async def execute(self, plan: CodingPlan, branch: str) -> CodingResult: ...
