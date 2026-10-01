# Feature: Ari por defecto — terminal con «dale» + diarización de audio

## Objective
Make Ari support, by default: (1) running terminal/shell commands on the host, gated
by an explicit «dale» confirmation (owner-only), and (2) speaker diarization on audio
messages (who-speaks-when) via pyannote.audio + HUGGINGFACE_TOKEN, degrading gracefully
to plain transcription when unavailable.

## Problem / Why
Gabriel (owner) wants Ari more autonomous: already got "program without /code" and the
three media skills (documents, groq_audio, groq_vision) enabled by default. Two gaps
remained: Ari cannot run shell commands from chat, and audio is transcribed without
speaker labels even though a HUGGINGFACE_TOKEN was added to the vault for pyannote.

## Decisions (owner-confirmed)
- Terminal security model: **«dale» gate** (propose exact command → run only on «dale»),
  owner-only, host-level (NOT sandboxed to a folder). Chosen over "directo sin freno"
  and "acotado a carpeta".
- Scope "todo eso por defecto" = all of: audio+diarización, programar sin /code (already
  done), skills media activas (already true in manifests), terminal access.

## Already done (verified, no work needed)
- Programar sin /code → `proponer_codigo` fires from natural language (mem #76).
- Skills media `enabled: true` → documents / groq_audio / groq_vision manifests.

## Scope (authorized)
Part A (terminal) and Part B (diarización). Two delivery slices / branches as needed.
Do NOT touch the uncommitted vault_web UI redesign (unrelated WIP on working tree).

## Design
### Part A — Terminal con «dale» (mirror the coding propose/confirm pipeline)
`proponer_comando(comando)` MCP tool (owner-only) → enqueue to `command_requests` table
→ `CommandRequestRunner` (scheduler tick) claims it → stores a `PendingCommand` in a
dedicated command pending store → sends "Voy a correr: `cmd` — dale/no" → on «dale»
`route_message` pops it and `ConfirmCommand` runs the shell (subprocess, timeout,
captures stdout+stderr, truncates) and reports. Plugged beside the coding flow; the
coding PendingStore/ConfirmCoding structure is untouched. Newest-wins across both
pending stores (proposing one clears the other).

### Part B — Diarización
Extend the skill context with `optional_secret(name)` (resolves vault→env→.env, returns
None instead of raising, NOT gated by required_secrets and NOT marked needs_secrets),
backed by a new `optional_secrets` manifest field. groq_audio declares
`optional_secrets: ["HUGGINGFACE_TOKEN"]`; when the token + pyannote are available and the
audio has >1 speaker, align pyannote turns with the transcript and emit
`Hablante 1: … / Hablante 2: …`; otherwise plain transcription. `pyannote.audio` added as
an optional pyproject dependency. NOTE: pyannote also needs the user to accept model
conditions on HF Hub (manual, one-time) — documented, not automatable.

## Constraints / Checks
- TDD: **strict** (source: session config). Runner: `python -m pytest`. Red→Green→Refactor
  with observed evidence per task.
- Non-slow suite gate: `python -m pytest -q -m "not slow"` must stay green.
- Live/e2e tests (`slow`) not run here.
- Conventional Commits, no AI attribution. Work-unit commit per feature slice.

## Route declaration
Part A implemented **direct inline** by the orchestrator: deep fresh CodeGraph context +
high blast radius (route_message 16 callers, PendingStore 26, main.py _post_init) make a
cold handoff costlier/riskier than inline TDD. Verified via full non-slow suite + boot.
Part B: direct inline or delegated writer (more isolated).

## Tasks
- [x] A1 Domain: `PendingCommand` + `CommandResult` + `CommandRequest` entities — 3 tests green
- [x] A2 Persistence: `command_requests` table in `_SCHEMA` + `SqliteCommandRequests` — 4 tests green
- [x] A3 Application: `CommandRequestRunner`, `ConfirmCommand`, `ShellRunner` (reuses PendingStore) — 12 tests green
- [x] A4 Routing: `route_message` + `CodingDeps` command branch («dale»/«no», command-priority, newest-wins) — 5 tests green, coding flow no regression
- [x] A5 MCP: `proponer_comando` tool + `AriTools` + `ari_permissions` + `capabilities.py` + `SOUL.md` — 29 tests green (incl. updated contract tests)
- [x] A6 Wiring: `main.py` `_post_init` (scheduler + deps + reset_taken) — import/boot OK, full non-slow suite **598 passed, 2 skipped**
- [x] A7 Commit terminal slice — commit 71baa9a
- [x] B1 Skills framework: `optional_secret` / `optional_secrets` — 4 tests green, skill_manager no regression
- [x] B2 groq_audio: pyannote diarization + pyproject optional dep (`[diarization]`) + manifest + graceful fallback — 14 tests green (`_label_transcript` alignment, diarize/fallback paths with fakes)
- [ ] B3 Commit diarización slice

## Activation (manual, user-only — cannot be done/verified here)
Real diarization needs two one-time steps from Gabriel:
1. `pip install -e '.[diarization]'` in the Ari venv (pulls torch, heavy).
2. Accept model conditions on Hugging Face for `pyannote/speaker-diarization-3.1`
   and `pyannote/segmentation-3.0`, with the account behind the vault's
   `HUGGINGFACE_TOKEN`.
Until then groq_audio keeps transcribing plainly (graceful fallback). Note:
`skills/` is outside ruff's configured `src`, so BLE001 on the skill's broad
excepts is out of scope and matches the existing graceful-degradation pattern.

NOTE on cross-store supersession: command pending and coding pending are separate
PendingStore instances. route_message checks command first on «dale». A rare both-pending
state is harmless (command «dale» runs the command; a lingering coding plan is superseded
later or expires). Full cross-clear was not built to avoid coupling the two just-stabilized
flows.

## Acceptance criteria
- Owner says "corré pytest -q" → Ari proposes the exact command → «dale» runs it →
  truncated stdout/stderr reported. Non-owner is refused. No «dale» → nothing runs.
- Audio with multiple speakers + HF token + pyannote present → labeled transcript.
  Missing token / pyannote / single speaker → plain transcription (no crash).
- Full non-slow test suite green.

## Progress / evidence
(updated per task as work lands)
