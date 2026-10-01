# Feature: Close the coding loop (verify → merge → reload)

**Objective**: When Ari programs something (especially a new skill), it should end
up live and usable in the running bot — not stranded half-broken on an unmerged
branch. Ari writes on an isolated branch, installs new deps, runs the test suite,
and ONLY if tests pass merges into the live branch and hot-reloads skills.

**Problem (root cause, confirmed in code)**: the coding loop is open.
1. `ConfirmCoding` creates an ephemeral `ari/tg-<slug>` branch and ends with
   "push/merge queda para ti" — the running process never sees the code.
2. No dependency install — a generated import of a missing package makes the skill
   load as `failed` (skill_manager.py:163-165).
3. No verification — `claude_code_coder.execute` commits whatever the CLI left as
   long as `is_error` is false. Partial work reported as "Listo".
4. The coder is never given the skill-authoring contract, so generated skills miss
   the loader contract (skill.json name/entrypoint/factory, on_inbound, guarded heavy
   imports).

**Chosen approach (user, 2026-10-01)**: "Rama + verificar + merge auto". Write on an
isolated branch, install+test there, merge into the live branch only on green, else
leave the branch and report. Max safety: the live checkout never sees untested code.

**Route**: delegated direct (multiple non-trivial files; each task TDD'd by a bounded
writer). Orchestrator verifies by re-running the task test command.

**TDD**: strict (RED → GREEN → REFACTOR). Runner: `uv run pytest` (project uses uv +
.venv; the PATH `pytest` is a system 3.9 and must NOT be used).

**Verification command**: `uv run pytest -m "not slow" -q`.

**Environment facts**: uv at ~/.local/bin/uv; .venv present; deps in pyproject
`[project.optional-dependencies] dev` (pytest, pytest-asyncio, ruff). Heavy audio
deps under extra `diarization` (pyannote) — kept out of the fast verify via `-m "not slow"`.

## Tasks
- [ ] T1 — `CoderVerifier` (src/ari/infrastructure/coder/verifier.py): optional install
      when deps changed + `uv run pytest -m "not slow" -q`; injectable runner;
      returns `VerifyResult(ok, detail)` with a trimmed output tail. TDD: green, red,
      install-path, runner-exception. (+ tests/infrastructure/test_verifier.py)
- [ ] T2 — `Workspace` finalize helpers: `current_branch`, `merge_into(base, branch)`,
      `checkout(base)` — all root-guarded like create_branch. TDD with real git in a
      tmp repo (mirror test_workspace.py style). (+ tests/infrastructure/test_workspace.py)
- [ ] T3 — `SkillManager.reload()`: force `_rebuild()` + restamp, return active skill
      names; so the merge step can report what went live. TDD. (+ test_skill_manager.py)
- [ ] T4 — Wire `ConfirmCoding`: capture base branch before create_branch; after a
      successful execute → verify. Green → `merge_into` + `on_merged` reload callback +
      report "verde, mergeado a <base>, skill recargado". Red → `checkout(base)`, leave
      the branch, report the test tail. `verifier`/`on_merged` optional (None-safe) so
      existing coding-only callers keep working. (+ test_confirm_coding.py, coding_fakes.py)
- [ ] T5 — `main._post_init` wiring + settings: build `CoderVerifier` (test/install cmd,
      timeout from settings) and pass it + an `on_merged` that calls `c.skills.reload()`
      into `ConfirmCoding`. (+ settings, + tests touched)
- [ ] T6 (enhancement) — Inject the skill-authoring contract into the coder's exec
      prompt when the task is skill-related, so generated skills match the loader. TDD.

## Decisions / defaults
- Fast verify excludes `slow` tests (model/network) for determinism and speed.
- Install only when `pyproject.toml`/requirements appear in the branch's changed files.
- Merge target = the branch Ari was on when the task started (captured pre-branch).
- Reload leans on SkillManager mtime refresh; `reload()` forces it + reports names.

## Known risks
- The live Ari process runs from the same repo while git switches branches. Coder
  commits everything (via _collect_git) so checkout is clean; brief window on the
  ari/tg branch before merge is owner-only. Acceptable; revisit if it bites.
- Auto-installing LLM-added deps into the live env is additive but runs arbitrary
  packages (owner-only scope). Flagged for the user.

## Acceptance
- A skill task that passes tests ends merged on the live branch and reported as
  reloaded; the running bot can use it without restart.
- A skill task whose tests fail is NOT merged; the branch is left and the failure
  tail is reported.
- `uv run pytest -m "not slow" -q` green after each task.

## Progress / evidence
- (pending) feature doc created; branch feat/close-coding-loop.
