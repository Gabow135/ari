# Feature: Close the coding loop (verify → merge → reload)

**Objective**: When Ari programs something (especially a new skill), it should end
up live and usable in the running bot — not stranded half-broken on an unmerged
branch. Ari writes on an isolated branch, installs new deps, runs the test suite,
and ONLY if tests pass merges into the live branch and hot-reloads skills.

**Problem (root cause, confirmed in code)**: the coding loop is open.
1. `ConfirmCoding` created an ephemeral `ari/tg-<slug>` branch and ended with
   "push/merge queda para ti" — the running process never saw the code.
2. No dependency install — a generated import of a missing package made the skill
   load as `failed` (skill_manager.py:163-165).
3. No verification — `claude_code_coder.execute` committed whatever the CLI left as
   long as `is_error` is false. Partial work reported as "Listo".
4. The coder was never given the skill-authoring contract, so generated skills missed
   the loader contract (skill.json name/entrypoint/factory, on_inbound, guarded heavy imports).

**Chosen approach (user, 2026-10-01)**: "Rama + verificar + merge auto". Write on an
isolated branch, install+test there, merge into the live branch only on green, else
leave the branch and report. Max safety: the live checkout never sees untested code.

**Route**: delegated direct, each task TDD'd by a bounded writer; orchestrator verified
by re-running the task test command. T5 wiring + T6 finalized inline in an isolated
git worktree (see Incident below).

**TDD**: strict (RED → GREEN → REFACTOR). Runner: `uv run pytest` (project uses uv +
.venv; the PATH `pytest` is a system 3.9 and must NOT be used).

**Verification command**: `uv run pytest -m "not slow" -q`.

## Tasks
- [x] T1 — `CoderVerifier` (src/ari/infrastructure/coder/verifier.py): optional install
      when deps changed + `uv run pytest -m "not slow" -q`; injectable runner; returns
      `VerifyResult(ok, detail)`. 6 tests green. (commit 8970002)
- [x] T2 — `Workspace` finalize helpers `current_branch`/`checkout`/`merge_into`,
      root-guarded, `merge --abort` on conflict. 13 tests green. (commit dc0c86c)
- [x] T3 — `SkillManager.reload()`: force rebuild + restamp, return active skill names.
      11 tests green. (commit 7ff4404)
- [x] T4 — Wire `ConfirmCoding`: capture base before create_branch; execute → verify →
      green merges to base + on_merged reload + report; red leaves branch + reports tail.
      `verifier`/`on_merged` optional (None-safe). 8 tests green. (commit 7cd24ac)
- [x] T5 — `main._post_init` wiring: build `CoderVerifier(timeout=coding_timeout_seconds)`
      + `on_merged` calling `c.skills.reload()`; fixed the T1 timeout nit (default runner
      now uses `self._timeout`). Rebuilt clean on the worktree (see Incident).
- [x] T6 — Inject the skill-authoring contract (`skill_prompt.augment_for_skill`) into the
      coder's exec prompt when the task is skill-related, so generated skills match the
      loader and ship a test. 11 + 2 tests green.

## Incident (2026-10-01): live bot contaminated the shared checkout
While implementing, the LIVE Ari bot (PID 28176) ran its own coding task
("recall-precision"), did `git checkout -b feat/recall-precision` in the SAME repo, and
switched the working tree mid-work. Consequences:
- Commits T1–T5 (8970002..5857f87) landed on `feat/recall-precision` with `e00d1c9` as
  base; `feat/close-coding-loop` stayed at `e00d1c9`.
- My T5 `git add src/ari/main.py` swept the bot's uncommitted `recall_ranker` import +
  `RecallRanker(RankWeights(...))` wiring into commit 5857f87 (contamination).
- The bot left `recall_ranker.py`, `ari-recall-precision.md`, test files and `uv.lock`
  uncommitted in the main checkout.
This is EXACTLY the open-loop bug this feature fixes, observed live on the unfixed bot.
Resolution (user chose "isolated worktree"): finished in a worktree at
`<repo>-worktrees/close-coding-loop-t6`. Fast-forwarded `feat/close-coding-loop` to the
clean T4 (7cd24ac), brought T5's clean `verifier.py`, rebuilt the T5 main.py wiring on
the clean base (NO recall contamination), then added T6. The bot's recall-precision WIP
was left untouched in the main checkout.

## Known follow-ups for the user
- `feat/recall-precision` still contains T1–T5 commits (shared SHAs) plus the bot's
  uncommitted recall WIP — untangle before merging recall-precision.
- The live bot does NOT yet have this fix; until `feat/close-coding-loop` is merged and
  the bot restarted, it will keep writing to the shared checkout.
- Manual end-to-end still pending: have the (fixed) bot create a skill via Telegram and
  confirm it verifies, merges, and is usable without restart.

## Acceptance (met in unit/integration tests)
- Green skill task → merged on the live branch + reported reloaded.
- Red skill task → NOT merged; branch left; failure tail reported.
- `uv run pytest -m "not slow" -q` green.
