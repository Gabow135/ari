"""Persistent logging for Ari: one console handler plus a rotating file.

Ari used to call ``logging.basicConfig`` only, so every line went to the
controlling terminal and was lost once scrolled past. ``setup_logging`` adds a
rotating file so runtime behavior — chat-turn timeouts above all — can be
followed after the fact. It is idempotent: safe to call again without stacking
duplicate handlers.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(log_file: str, level: str = "INFO",
                  max_bytes: int = 5_000_000, backups: int = 5) -> None:
    """Configure root logging with a console handler and (if ``log_file``) a
    rotating file handler. Calling it more than once keeps exactly one of each.
    """
    root = logging.getLogger()
    root.setLevel(level.upper() if isinstance(level, str) else level)
    fmt = logging.Formatter(_FMT)

    console = next((h for h in root.handlers if type(h) is logging.StreamHandler), None)
    if console is None:
        console = logging.StreamHandler()
        root.addHandler(console)
    console.setFormatter(fmt)

    if not log_file:
        return
    path = os.path.abspath(os.path.expanduser(log_file))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    already = any(isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == path
                  for h in root.handlers)
    if not already:
        fh = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups,
                                 encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
