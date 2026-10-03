# Amazon Music integration — ON STANDBY

**Status:** Paused on 2026-10-03. Not wired into Ari. Revisit when Amazon Music
ships a usable public API.

## Why it is paused

Amazon Music has **no usable direct API** for a third-party bot:

- The official Music API is in **closed beta**.
- There is **no remote-control playback API**.
- DRM terms forbid running playback on platforms where the end user can install
  custom software (i.e. a bot is excluded).

Source: https://developer.amazon.com/docs/music/API_playback_overview.html

The only working path today is **browser automation** (driving the Amazon Music
web player in a real Chrome via CDP). That is fragile and plays audio only on the
machine Ari runs on, so it was intentionally left out of `main`.

## What already exists (kept for resume)

- Clone + dedicated venv: `~/tools/amazon-music-mcp` (fork of
  `mk-8/amazon-music-mcp`).
- `mcp` is **pinned to `>=1.0,<2`** in that clone's `pyproject.toml`. The code is
  written for the MCP 1.x low-level API (`from mcp.server import Server`,
  `@app.list_tools()`); MCP 2.x removes it and breaks the server.
- Exposed tools: `search_and_play`, `play`, `pause`, `next_track`.

## Resume recipe (when Amazon has a real API, or to accept browser automation)

Prefer a future official API. If still using the browser-automation MCP:

1. Launch a dedicated Chrome with remote debugging, logged into Amazon Music
   (keep it open; it must be the first tab):

   ```
   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
     --remote-debugging-port=9222 \
     --user-data-dir="$HOME/.amazon-music-chrome" \
     --no-first-run --no-default-browser-check \
     "https://music.amazon.com"
   ```

2. Add this entry to `mcp/servers.json` (owner-only):

   ```json
   "amazon-music": {
     "command": "/Users/gabow135/tools/amazon-music-mcp/.venv/bin/python",
     "args": ["-m", "amazon_music_mcp"],
     "access": "owner",
     "description": "Buscar y reproducir música en Amazon Music (requiere Chrome abierto en music.amazon.com con --remote-debugging-port=9222)"
   }
   ```

   Ari's `McpRegistry` hot-reloads `servers.json`, so no restart is needed.

## Known gotchas

- **Per-turn cost:** Ari launches every configured MCP server per turn. Only add
  this entry when actively using it — Ari is timeout-prone and a dormant
  browser-automation server adds startup overhead each turn.
- **CDP port 9222 is hardcoded** in `music.py` (`connect_over_cdp`), and it
  attaches to `contexts[0].pages[0]` — Amazon Music must be the first/only tab.
- **Selectors are hardcoded in English** in `~/tools/amazon-music-mcp/amazon_music_mcp/music.py`
  (`button[aria-label='Play']`, `input[placeholder='Search']`). If the account's
  web UI is in Spanish they will not match (`'Reproducir'`, `'Buscar'`) — patch
  them there (editable install, no reinstall needed).

## Cleaner alternative if the provider is negotiable

**Spotify** has an official Web API (search + playback via Spotify Connect) and
mature MCP servers — no browser, plays on any active Spotify device. Playback
control requires Spotify Premium. If the goal ("search and play music") matters
more than the provider, Spotify is the architecturally sound choice.
