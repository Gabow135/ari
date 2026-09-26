"""A filesystem MCP root must not let Ari reach any secret. A root is unsafe when
it equals, or is an ancestor of, a sensitive path (so that path lies inside it).
Symlinks are resolved so a link into the project cannot slip past."""
import os
from collections.abc import Iterable


def _real(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def unsafe_fs_root(root: str, sensitive: Iterable[str]) -> str | None:
    root_real = _real(root)
    for s in sensitive:
        s_real = _real(s)
        if s_real == root_real or s_real.startswith(root_real + os.sep):
            return s  # original (unexpanded) form, for a readable message
    return None
