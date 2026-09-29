#!/usr/bin/env bash
# Launch Ari with the project virtualenv so the `ari` MCP server (memory,
# agenda, missions) and all deps (mcp, fastembed, …) are available.
# Do NOT run `python3 -m ari.main` directly: the system python has no deps.
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python -m ari.main "$@"
