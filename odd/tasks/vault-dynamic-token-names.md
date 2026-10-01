# Feature: Ari registers new vault token names from conversation

## Objective
Let Ari add brand-new credential names (not pre-wired in `mcp/servers.json` and not
declared by any installed skill) to the vault web form, driven by natural language.
When the owner says "pasame el token de Notion", Ari passes the concrete VAR name
(e.g. `NOTION_API_KEY`) through `pedir_credenciales`; the credential runner registers
it on the vault maintainer so the next (and current) link's form shows it and accepts
its POST.

## Problem / Why
Today the vault web allowlist is static: `configurable_secret_names(servers.json)` ∪
`skills.required_secret_names()`, minus `ARI_FS_ROOT`. A POST with any other name is
rejected 400. So a genuinely new token cannot be loaded. Owner chose (AskUserQuestion):
"Ari los registra desde la charla" — a dynamic allowlist that Ari grows per request,
keeping a guard (not a free-form escape hatch).

## Scope
- IN: parse Ari's requested names → UPPER_SNAKE vault names; a dynamic registered-names
  set owned by `VaultWebMaintainer`; refresh the running server's allowlist; runner
  registers before minting the link; tighten `pedir_credenciales` guidance.
- OUT (note to owner): wiring a new token into `servers.json`/a skill so something
  actually *reads* it. Storing the value is in scope; consuming it is a later step.

## Constraints
- TDD strict (runner: `.venv/bin/python -m pytest`). RED before GREEN per task.
- Keep the security posture: still an allowlist (now dynamic). Never writable:
  `ARI_FS_ROOT`. Values are never echoed (unchanged).
- Technical artifacts in English; the `pedir_credenciales` docstring stays Spanish
  (existing tool-catalog convention Ari reads).
- Thread-safety: the maintainer runs under a `threading.Lock`; `_writable_names()` must
  NOT take the lock (non-reentrant).

## Tasks
- [x] T1 — `normalize_secret_names(raw)` in new module
  `src/ari/application/credentials/requested_names.py`: split on commas/whitespace,
  uppercase, keep UPPER_SNAKE tokens with ≥1 underscore (`^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$`),
  drop `ARI_FS_ROOT`, dedup (stable order), cap count (20) and length (64). Prose with no
  var-shaped token → `[]`. Tests: `tests/application/test_requested_names.py`.
  Evidence: 9 tests RED→GREEN (ModuleNotFoundError → 9 passed).
- [x] T2 — `VaultWebMaintainer.register_names(names)` +
  `_writable_names()` includes a `self._requested` set + `new_link()` refreshes the live
  server's names. `register_names` holds `self._lock`, updates the server when running.
  Tests: extend `tests/infrastructure/test_vault_web_extra_names.py` (registered names
  appear; `ARI_FS_ROOT` never does; dedup with config/extra).
  Evidence: 3 tests RED (AttributeError) → 6 passed.
- [x] T3 — `VaultWebServer.set_names(names)` updates `self._names` and `self._httpd.names`
  (when running) so the handler sees the new allowlist on the next request. Test in
  `tests/infrastructure/vault_web/test_server.py`: after `set_names`, GET shows the new
  name and POST of it is accepted (200), not 400.
  Evidence: 2 tests RED (AttributeError) → 16 passed.
- [x] T4 — `CredentialRequestRunner` registers before minting:
  `self._vault_web.register_names(normalize_secret_names(req.requested))` ahead of
  `new_link()`. Updated fake records `register_names`; new test confirms registered.
  Evidence: 1 test RED (assert [] == [['NOTION_API_KEY']]) → 4 passed.
- [x] T5 — `pedir_credenciales` docstring (`src/ari/mcp_server/server.py`): updated to
  instruct Ari to pass UPPER_SNAKE variable names, comma-separated, keeping Spanish tone.
  Evidence: docstring updated; full suite 573 passed, 2 skipped.

## Acceptance
- New module tested; registered names flow end-to-end to the form allowlist and POST.
- Full suite green: `.venv/bin/python -m pytest -m "not slow"`.
- No main.py / Components change required (maintainer owns the set; runner already has it).

## Checks
- `.venv/bin/python -m pytest tests/application/test_requested_names.py tests/infrastructure/test_vault_web_extra_names.py tests/infrastructure/vault_web/test_server.py tests/application/test_credential_request_runner.py -q`
- Then the full `-m "not slow"` suite.

## Route
- T1 inline-sized but delegated together with T2–T5 as ONE bounded writer (writer trigger:
  5 non-trivial files). TDD per task. Parent verifies (full suite + diff review).

## TDD mode
- Enabled (strict). Source: session config. Runner: `.venv/bin/python -m pytest`.
