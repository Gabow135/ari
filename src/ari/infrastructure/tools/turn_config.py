import logging
import os
import time
import uuid

from ari.infrastructure.tools.files import atomic_write_json

log = logging.getLogger("ari.mcp")


class TurnConfigWriter:
    """One MCP config file per Claude call (it carries that turn's identity);
    removed as soon as the call ends."""

    def __init__(self, out_dir: str):
        self._out_dir = out_dir

    def write(self, servers: dict) -> str | None:
        path = os.path.join(self._out_dir, f"turn-{uuid.uuid4().hex}.json")
        try:
            atomic_write_json(path, {"mcpServers": servers})
        except OSError as exc:
            log.error("could not write turn config %s: %s", path, exc)
            return None
        return path

    def remove(self, path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.error("could not remove turn config %s: %s", path, exc)

    def sweep(self, older_than_seconds: int = 600) -> int:
        """Deletes orphaned ``turn-*.json``/``turn-*.json.tmp`` files older than
        ``older_than_seconds`` (a crash between write and the ``finally`` that
        removes them, e.g. a killed process, leaves them behind). Only matches
        that exact naming pattern: owner.json/users.json and anything else in
        the directory are never touched. Returns how many files were removed."""
        try:
            names = os.listdir(self._out_dir)
        except OSError:
            return 0
        cutoff = time.time() - older_than_seconds
        removed = 0
        for name in names:
            if not (name.startswith("turn-") and name.endswith((".json", ".json.tmp"))):
                continue
            path = os.path.join(self._out_dir, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError as exc:
                log.error("could not sweep turn config %s: %s", path, exc)
        return removed
