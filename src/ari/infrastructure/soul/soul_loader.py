import logging
import os

log = logging.getLogger("ari.soul")


class SoulLoader:
    """Reads ``<soul_dir>/<filename>`` (default ``SOUL.md``); re-reads it only when the file changes, so
    edits apply on the next message without a restart. Missing -> None."""

    def __init__(self, soul_dir: str, filename: str = "SOUL.md"):
        self._path = os.path.join(soul_dir, filename)
        self._stamp: tuple[int, int] | None = None
        self._text: str | None = None
        self._warned_missing = False

    def __call__(self) -> str | None:
        try:
            st = os.stat(self._path)
        except OSError:
            if not self._warned_missing:
                log.warning("soul file not found at %s; using default identity", self._path)
                self._warned_missing = True
            self._stamp, self._text = None, None
            return None
        self._warned_missing = False
        stamp = (st.st_mtime_ns, st.st_size)
        if stamp != self._stamp:
            with open(self._path, encoding="utf-8") as f:
                self._text = f.read()
            self._stamp = stamp
        return self._text
