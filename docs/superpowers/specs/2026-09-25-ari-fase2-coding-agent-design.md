# Ari — Phase 2: Telegram-driven Coding Agent (Design)

- **Date:** 2026-09-25
- **Status:** Draft — pending user approval
- **Builds on:** Phase 1 (`docs/superpowers/specs/2026-09-24-ari-fase1-gateway-memoria-design.md`)
- **Scope:** Give the owner the ability to instruct Ari over Telegram to write/modify
  code ("program what's needed", including working on Ari's own next phases),
  executed by Claude Code with tools, behind strict safety gates.

## 1. Purpose

Extend Ari so the **owner** can send a coding instruction over Telegram and Ari
will drive **Claude Code (with tools)** to carry it out — plan first, execute on
confirmation, on an isolated git branch, and report back. Success: the owner
messages `/code add a healthcheck endpoint` (or `/fase2 ...`), Ari replies with a
short plan, the owner replies `dale`, and Ari produces commits on a new branch in
the target repository and reports what it did. Push/PR/merge remain manual.

This is the coding-agent flavor of the original Phase 2 ("tool-calling"). A
general tool registry is explicitly out of scope (YAGNI); coding is the one
capability delivered here, cleanly isolated so more tools can be added later.

## 2. Locked decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Capability | Coding agent — Claude Code **with tools** (Read/Edit/Write/Bash) | User wants Ari to actually program what's needed |
| Scope of code it may touch | Ari's own repo **and** a configurable workspace, always inside an allowed root | User picked "both"; enables self-development + other projects |
| Authorization | **Owner-only** allowlist of Telegram user IDs (`ARI_OWNER_IDS`) | Arbitrary code execution from a chat MUST be owner-gated |
| Control model | **Plan → confirm → execute** (human-in-the-loop per task) | User chose the safest model |
| Isolation | Always a **fresh git branch**; never commit to the default branch; **never auto-push** | Blast-radius containment; delivery stays manual |
| Sandbox boundary | Every target path must resolve **inside `ARI_ALLOWED_ROOT`** | Prevents touching arbitrary disk locations / path traversal |
| Architecture | New `CoderPort` + adapter + application coding use cases (hexagonal) | Keeps the dangerous capability behind a clean, testable boundary |
| Execution | Background asyncio task; one coding job per user at a time | Long coding runs must not block the bot |

## 3. Architecture

Additive to Phase 1's hexagonal layout. The chat path (Phase 1 `HandleMessage`)
is unchanged; a `CommandRouter` sits in front and forwards coding instructions to
the new coding use cases. The dangerous capability (Claude Code with tools) lives
in a single infrastructure adapter behind a domain `CoderPort`.

### 3.1 Directory additions

```
src/ari/
  domain/coding/
    instruction.py        # CodingInstruction(user_id, text, target_spec)
    plan.py               # CodingPlan(summary, steps, target_dir, branch)
    result.py             # CodingResult(branch, changed_files, commits, test_summary, ok, detail)
    pending.py            # PendingAction(user_id, plan) — awaiting confirmation
    coder_port.py         # CoderPort protocol (plan + execute)
  application/
    command_router.py     # classify inbound message; owner check; route chat vs coding
    coding/
      __init__.py
      authorizer.py       # Authorizer(owner_ids): is_owner(user_id) -> bool
      request_coding.py   # instruction -> CoderPort.plan -> reply plan + store PendingAction
      confirm_coding.py   # confirmation -> background execute -> report
      pending_store.py    # in-memory per-user PendingAction store (+ "busy" guard)
  infrastructure/coder/
    __init__.py
    claude_code_coder.py  # CoderPort impl: `claude -p` WITH tools, in target dir, on a branch
    workspace.py          # resolve + validate target dir within ARI_ALLOWED_ROOT; git branch ops
```

### 3.2 `CoderPort` (domain contract)

```
async def plan(self, instruction: CodingInstruction, target_dir: str) -> CodingPlan
async def execute(self, plan: CodingPlan, instruction: CodingInstruction) -> CodingResult
```

- `plan` runs Claude Code in a **non-mutating planning mode** and returns a short
  human-readable plan (what it intends to do). It must not modify files.
- `execute` runs Claude Code **with tools** in `plan.target_dir`, on `plan.branch`,
  and returns a `CodingResult` (branch, changed files, commit shas, optional test
  summary, ok flag, detail/errors).

## 4. Message flow

```
inbound message
  → CommandRouter:
      is_coding = starts with /code or /fase2, OR (owner AND natural coding intent)
      if not is_coding → Phase 1 HandleMessage (chat, unchanged)
      if is_coding:
         if not Authorizer.is_owner(user_id) → decline politely, stay in chat
         else:
            target_dir = Workspace.resolve(target_spec)  # validated inside ARI_ALLOWED_ROOT
            plan = await CoderPort.plan(instruction, target_dir)
            PendingStore.put(user_id, PendingAction(plan))
            reply: plan summary + "Respondé 'dale' para ejecutar, 'no' para cancelar."
  → next message from that user:
      if PendingStore.has(user_id):
         if confirmation ('dale'/'sí'/'ok') →
            branch = create fresh branch in target_dir
            reply: "Arranco en branch <branch>…"
            schedule background: CoderPort.execute(plan) → report CodingResult
            PendingStore.clear(user_id)
         if cancel ('no'/'cancelar') → PendingStore.clear ; reply "Cancelado."
         else → treat as a new instruction (re-plan) or chat
```

Confirmation matching is intentionally strict (an explicit affirmative token), so
an ambiguous reply never triggers execution.

## 5. Security model (the core of Phase 2)

1. **Owner-only execution.** Only Telegram user IDs in `ARI_OWNER_IDS` may trigger
   coding. Non-owners get a polite decline; Phase 1 chat still works for them
   (chat-for-all is the default; can be tightened later).
2. **Sandbox root.** `Workspace.resolve` canonicalizes the requested target and
   asserts it is inside `ARI_ALLOWED_ROOT` (realpath containment check). Requests
   resolving outside are refused. Path-traversal attempts (`..`, symlinks,
   absolute escapes) are rejected. This is security-critical and gets dedicated
   tests.
3. **Branch isolation.** Execution always happens on a **new branch** created from
   the target's current HEAD (e.g. `ari/tg-<short-slug>`); Ari never commits to the
   default branch and **never pushes**. The owner reviews and delivers.
4. **Confirmation gate.** Nothing with side effects runs before an explicit
   owner confirmation of the shown plan.
5. **No secret leakage.** Instructions/plans/results are logged without tokens;
   the coder subprocess receives no secrets on the command line.

## 6. Coder adapter (`ClaudeCodeCoder`)

- Reuses the Phase 1 pattern: an **injectable async runner** so unit tests never
  invoke the real CLI.
- `plan(...)`: invoke `claude -p` in planning mode — e.g.
  `claude -p "<instruction>\n\nProduce a concise plan only; do not modify files." --permission-mode plan --output-format json`
  (verify the exact planning flag against the installed CLI; fall back to a
  tools-disabled prompt that asks for a plan). Returns the plan text.
- `execute(...)`: run in `target_dir` with tools enabled and a bounded, non-empty
  allowlist:
  `claude -p "<instruction>" --allowed-tools "Read,Edit,Write,Bash" --permission-mode acceptEdits --output-format json`
  Headless mode cannot prompt for permissions, so a non-interactive permission
  posture (`acceptEdits`, or an explicit allowlist) is required; the branch +
  no-push + owner-only gates are what make this acceptable. After the run, ensure
  the work is committed on `plan.branch` (Claude commits, or the adapter stages +
  commits leftover changes), and collect `git` stat/log for the report.
- Model: `ARI_CODER_MODEL` (default a strong model, e.g. Sonnet; overridable).

## 7. Background execution & reporting

- Coding runs can take minutes → run as an `asyncio.create_task`, keep a reference
  (avoid GC), and enforce **one active job per user** (a busy guard).
- Telegram feedback: an immediate "starting on branch X" message, then a final
  report (branch, files changed, commits, test summary if run, errors on failure).
  Progress streaming (`--output-format stream-json`) is optional; MVP = start +
  final. A configurable **timeout** bounds the run; on timeout, report and leave
  the branch for inspection.

## 8. Configuration (additions)

- `ARI_OWNER_IDS` — comma-separated Telegram user IDs allowed to run coding
  (required to enable coding; if unset, coding is disabled and Ari stays chat-only).
- `ARI_ALLOWED_ROOT` — directory the coder may operate within (default: the parent
  of the Ari repo, so both Ari and sibling projects are reachable).
- `ARI_CODER_MODEL` — model for coding (default: a strong Claude model).
- `ARI_CODING_TIMEOUT_SECONDS` — max seconds for a coding run (default e.g. 900).
- Reuses `ARI_CLAUDE_BIN`.

## 9. Error handling

- Non-zero CLI exit / tool error → report failure with the tail of stderr; leave
  the branch intact for inspection; never crash the bot.
- Timeout → report; branch preserved.
- Non-owner coding attempt → polite decline, logged.
- Target outside `ARI_ALLOWED_ROOT` or invalid → refuse with a clear message.
- Confirmation with no pending action → treated as a normal message.
- All Telegram sends are best-effort; failures are logged, not fatal.

## 10. Testing (TDD — strict)

- **Application/domain (fakes):** `CommandRouter` classification (chat vs
  `/code` vs natural intent); `Authorizer` owner allow/deny; plan→confirm→execute
  state machine and `PendingStore` lifecycle incl. busy guard; confirmation token
  matching (only explicit affirmatives execute).
- **Security:** `Workspace.resolve` — accepts inside-root paths, **rejects**
  `..` traversal, absolute escapes, and paths outside `ARI_ALLOWED_ROOT`. These
  are must-have tests.
- **Coder adapter:** injectable fake runner for unit tests (plan text parsing,
  execute result mapping, error path); a `@slow` integration test that runs the
  real `claude` CLI with tools on a **temporary git repo** and asserts a commit
  appears on the branch.
- Runner: `python3 -m pytest` (`-m "not slow"` by default).

## 11. Out of scope (later)

- General tool registry / multi-tool dispatch (curated tools beyond coding).
- Multi-user coding (non-owner execution), roles/permissions.
- Auto-push / auto-PR / auto-merge (delivery stays manual and owner-owned).
- Streaming token-by-token progress (MVP reports start + final).
- Container/VM sandboxing beyond the allowed-root + branch model.

## 12. Acceptance criteria

1. An owner sending `/code <instruction>` (or `/fase2 <instruction>`) receives a
   plan and is asked to confirm; nothing is modified before confirmation.
2. On `dale`, Ari creates a new branch in the (allowed, validated) target repo,
   runs Claude Code with tools, commits there, and reports branch + changes.
3. A non-owner cannot trigger coding (declined); normal chat still works.
4. A target path outside `ARI_ALLOWED_ROOT` (incl. `..` traversal) is refused.
5. Ari never commits to the default branch and never pushes automatically.
6. A coding run does not block the bot from handling other messages, and a
   failure/timeout is reported without crashing the bot.
7. Domain/application and the path-validation logic pass tests using fakes, with
   no network or real CLI in the default suite.

## 13. ToS note

Automating a personal Claude Code subscription to perform autonomous coding from
a bot is a Terms-of-Service gray area. Owner-only + personal use mitigates it; any
use beyond personal (e.g. serving a team) must revisit the auth/licensing model
before enabling coding for others.
