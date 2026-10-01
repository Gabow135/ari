# Feature: Source installer + GitHub Release (Hermes model)

**Objective**: Let users install Ari on Linux/macOS/WSL2 and native Windows with a
single one-liner, the way Hermes Agent / OpenCode do it. A GitHub Release `v0.0.1`
is created on tag push and ships the install scripts as assets.

**Problem**: Ari is a self-hosted Python+Node agent (venv, native deps like
fastembed/onnxruntime + sqlite-vec, the Claude Code CLI, uvx/npx MCP servers).
Per-OS PyInstaller binaries are fragile here. A "source installer" that bootstraps
prerequisites fits Ari exactly.

**Why source installer, not binaries**: One release = three platforms. We ship 2
scripts + source; the scripts resolve every OS. No build matrix, no bundling of
onnxruntime/model downloads.

**Route**: direct inline (the 5 artifacts are tightly coupled — installers ↔
workflow ↔ README URLs ↔ version — and quality-critical; one hand keeps them
coherent). TDD: N/A (shell/CI scaffolding, no unit-testable Python behavior added).
Verification = `bash -n` on install.sh + manual review of install.ps1 (no pwsh/
shellcheck available locally; disclosed).

**Repo**: github.com/Gabow135/ari · install dir `~/.ari` · launcher `ari`.

**Prerequisites the installer ensures**: git, Python ≥3.11, Node/npm, uv (uvx),
Claude Code CLI.

## Tasks
- [ ] T1 — pyproject: version 0.1.0 → 0.0.1; add `[project.scripts] ari = "ari.main:main"`.
- [ ] T2 — install.sh (Linux/macOS/WSL2): detect OS + PM (brew/apt/dnf/pacman/zypper),
      ensure prereqs, clone/update at ref, uv venv + editable install, `ari` launcher
      in ~/.local/bin, copy .env.example, print next steps. curl|bash-safe (non-interactive).
- [ ] T3 — install.ps1 (Windows native): winget/scoop, same flow, `ari.cmd` + user PATH.
- [ ] T4 — .github/workflows/release.yml: on `push: tags: v*` (+ workflow_dispatch),
      create Release with generated notes, attach install.sh + install.ps1.
- [ ] T5 — README: "Quick Install" section with the two one-liners.

## Defaults accepted by user
- Install URL = raw.githubusercontent.com/Gabow135/ari/main/ (zero infra; domain later).
- Heavy voice/audio prereqs (ffmpeg, pyannote) stay optional + documented.
- `.env` copied from `.env.example`, no interactive secret prompting.

## Acceptance
- `bash -n install.sh` clean.
- Workflow valid YAML, triggers on `v*`, uploads both scripts.
- README shows working one-liners (resolve after merge to main).
- pyproject version == tag (0.0.1).

## Go-live (user actions, outside this branch)
1. Merge `feat/source-installer` → main (so raw URL resolves).
2. `git tag v0.0.1 && git push origin v0.0.1` → workflow creates the Release.

## Progress / evidence
- (pending commit on feat/source-installer)
