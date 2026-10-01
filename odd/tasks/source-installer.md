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
- [x] T1 — pyproject: version 0.1.0 → 0.0.1; add `[project.scripts] ari = "ari.main:main"`.
- [x] T2 — install.sh (Linux/macOS/WSL2): detect OS + PM (brew/apt/dnf/pacman/zypper),
      ensure prereqs, clone/update at ref, uv venv + editable install, `ari` launcher
      in ~/.local/bin, copy .env.example, print next steps. curl|bash-safe (non-interactive).
- [x] T3 — install.ps1 (Windows native): winget/scoop, same flow, `ari.cmd` + user PATH.
- [x] T4 — .github/workflows/release.yml: on `push: tags: v*` (+ workflow_dispatch),
      create Release with generated notes, attach install.sh + install.ps1.
- [x] T5 — README: "Quick Install" section with the two one-liners.

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
- All tasks T1–T5 done. Commit c8b21af on feat/source-installer.
- Verification: `bash -n install.sh` → SYNTAX OK; release.yml → YAML OK.
- Not verified locally: install.ps1 (no pwsh/shellcheck available) — manual review only.
- SHIPPED: PR #1 merged to main (merge 75910a3); tag v0.0.1 pushed; Release
  workflow run 36882662351 succeeded (18s). Release "Ari v0.0.1" published with
  install.sh + install.ps1 assets: https://github.com/Gabow135/ari/releases/tag/v0.0.1
- Follow-up: validate install.ps1 on a real Windows host (not runnable locally).
