import logging
import os
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
