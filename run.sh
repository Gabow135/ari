#!/usr/bin/env bash
# Launch Ari with the project virtualenv so the `ari` MCP server (memory,
# agenda, missions) and all deps (mcp, fastembed, neonize, …) are available.
# Do NOT run `python3 -m ari.main` directly: the system python has no deps.
#
# This script (re)installs the project — including the optional `whatsapp`
# extra (neonize) — stops any Ari already running, then relaunches, so a
# restart always picks up new code and new dependencies.
#
# Tunables:
#   ARI_SKIP_INSTALL=1   skip the dependency (re)install step for a fast restart
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '==> %s\n' "$*" >&2; }

[ -x .venv/bin/python ] || {
  printf 'error: no virtualenv at .venv — run install.sh first.\n' >&2
  exit 1
}

# 1. (Re)install the package with the WhatsApp extra so neonize is present.
#    An already-satisfied install is a near no-op, so this is cheap on repeat runs.
if [ "${ARI_SKIP_INSTALL:-0}" != "1" ]; then
  say "installing dependencies (with 'whatsapp' extra)…"
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python .venv/bin/python -e '.[whatsapp]'
  else
    .venv/bin/python -m pip install -e '.[whatsapp]'
  fi
fi

# 2. Stop any Ari already running from this install. The pattern matches the
#    running bot (`python -m ari.main`), never this script (`bash run.sh`) nor
#    the pip step above.
PATTERN='python.* -m ari\.main'
if pgrep -f "$PATTERN" >/dev/null 2>&1; then
  say "stopping running Ari…"
  pkill -f "$PATTERN" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -f "$PATTERN" >/dev/null 2>&1 || break
    sleep 0.3
  done
  pkill -9 -f "$PATTERN" 2>/dev/null || true
fi

# 3. Relaunch Ari in the foreground (replaces this shell).
say "starting Ari…"
exec .venv/bin/python -m ari.main "$@"
