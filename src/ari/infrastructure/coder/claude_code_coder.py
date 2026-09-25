import asyncio
import json
import logging

from ari.domain.coding.entities import CodingInstruction, CodingPlan, CodingResult

log = logging.getLogger("ari.claude_code_coder")


class ClaudeCodeCoder:
    """CoderPort adapter backed by the Claude Code CLI.

    Uses the ``claude`` binary (must be on PATH or given via ``claude_bin``).
    Authentication comes from the CLI's own session — no ANTHROPIC_API_KEY
    required.

    Args:
        model: Model identifier forwarded to the CLI via ``--model``.
        claude_bin: Path or name of the ``claude`` binary.
        timeout: Maximum seconds to wait for any single CLI invocation.
        plan_runner: Injectable async callable ``(instr_text, target_dir, model) -> str``
            that returns the raw JSON string from the CLI.  Defaults to the
            real subprocess runner.  Override in tests to avoid calling the CLI.
        exec_runner: Same signature as ``plan_runner`` but for execution mode
            (tools enabled).  Defaults to the real subprocess runner.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        claude_bin: str = "claude",
        timeout: int = 900,
        plan_runner=None,
        exec_runner=None,
    ):
        self._model = model
        self._bin = claude_bin
        self._timeout = timeout
        self._plan_runner = plan_runner or self._default_plan_runner
        self._exec_runner = exec_runner or self._default_exec_runner

    # ------------------------------------------------------------------
    # CoderPort interface
    # ------------------------------------------------------------------

    async def plan(self, instruction: CodingInstruction, target_dir: str) -> CodingPlan:
        """Ask Claude to produce a step plan (read-only, no file mutations)."""
        raw = await self._plan_runner(instruction.text, target_dir, self._model)
        summary = self._parse_result(raw) or "(no plan)"
        log.debug("plan summary: %s", summary[:200])
        return CodingPlan(
            summary=summary,
            target_dir=target_dir,
            instruction_text=instruction.text,
        )

    async def execute(self, plan: CodingPlan, branch: str) -> CodingResult:
        """Execute the plan using the Claude CLI with editing tools enabled."""
        try:
            raw = await self._exec_runner(
                plan.instruction_text, plan.target_dir, self._model
            )
            data = json.loads(raw)
            if data.get("is_error"):
                detail = str(data.get("result", ""))[:500]
                log.warning("claude exec returned is_error=true: %s", detail)
                return CodingResult(ok=False, branch=branch, detail=detail)
        except Exception as exc:
            log.error("exec runner raised: %s", exc)
            return CodingResult(ok=False, branch=branch, detail=str(exc)[:500])

        changed, commits = await self._collect_git(plan.target_dir)
        log.info("execute ok: %d changed files, %d commits", len(changed), len(commits))
        return CodingResult(
            ok=True,
            branch=branch,
            changed_files=changed,
            commits=commits,
        )

    # ------------------------------------------------------------------
    # Default subprocess runners
    # ------------------------------------------------------------------

    async def _default_plan_runner(self, text: str, target_dir: str, model: str) -> str:
        argv = [
            self._bin,
            "-p",
            f"{text}\n\nProduce a concise step plan only; do NOT modify files.",
            "--model", model,
            "--permission-mode", "plan",
            "--output-format", "json",
        ]
        return await self._run(argv, cwd=target_dir)

    async def _default_exec_runner(self, text: str, target_dir: str, model: str) -> str:
        argv = [
            self._bin,
            "-p", text,
            "--model", model,
            "--allowed-tools", "Read", "Edit", "Write", "Bash",
            "--permission-mode", "acceptEdits",
            "--output-format", "json",
        ]
        return await self._run(argv, cwd=target_dir)

    async def _run(self, argv: list[str], cwd: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise RuntimeError(
                f"claude timed out after {self._timeout}s"
            )
        if proc.returncode != 0:
            raise RuntimeError(
                f"claude failed (exit {proc.returncode}): "
                f"{err.decode(errors='replace')[:400]}"
            )
        return out.decode(errors="replace")

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------

    async def _collect_git(self, target_dir: str) -> tuple[list[str], list[str]]:
        """Commit any leftover dirty files, then return changed files + recent commits."""

        async def git(*args: str) -> str:
            p = await asyncio.create_subprocess_exec(
                "git", "-C", target_dir, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            o, _ = await p.communicate()
            return o.decode(errors="replace").strip()

        status = await git("status", "--porcelain")
        if status:
            await git("add", "-A")
            await git("commit", "-m", "chore: apply Ari coding task")

        files = [line[3:] for line in status.splitlines()] if status else []
        log_out = await git("log", "--oneline", "-5", "--format=%h")
        commits = [c for c in log_out.splitlines() if c]
        return files, commits

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_result(raw: str) -> str:
        try:
            return (json.loads(raw).get("result") or "").strip()
        except Exception:
            return (raw or "").strip()
