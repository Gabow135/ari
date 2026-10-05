import os
import re

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _sanitize_id(user_id: str) -> str:
    cleaned = _UNSAFE.sub("_", (user_id or "").strip())
    # Never let a sanitized id become empty, ".", or ".." — all would break the jail.
    return cleaned if cleaned not in ("", ".", "..") else "_"


class UserWorkspace:
    """A path-jailed per-user directory. Every op is resolved with realpath and
    rejected if it escapes the user's root (blocks '..' and symlink escapes)."""

    def __init__(self, base_dir: str, user_id: str, *, max_file_bytes: int = 1_048_576):
        base = os.path.realpath(os.path.expanduser(base_dir))
        self._root = os.path.join(base, _sanitize_id(user_id))
        self._max = max_file_bytes

    @property
    def root(self) -> str:
        return self._root

    def ensure(self) -> str:
        os.makedirs(self._root, exist_ok=True)
        return self._root

    def resolve(self, rel: str) -> str:
        raw = (rel or "").strip()
        if os.path.isabs(raw):
            raise ValueError(f"absolute path not allowed: {raw}")
        path = os.path.realpath(os.path.join(self._root, raw))
        root = os.path.realpath(self._root)
        if path != root and not path.startswith(root + os.sep):
            raise ValueError(f"path escapes workspace: {raw}")
        return path

    def write_text(self, rel: str, content: str) -> str:
        data = (content or "").encode("utf-8")
        if len(data) > self._max:
            raise ValueError(f"content too large (> {self._max} bytes)")
        self.ensure()
        path = self.resolve(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content or "")
        return os.path.relpath(path, os.path.realpath(self._root))

    def read_text(self, rel: str) -> str:
        path = self.resolve(rel)
        if not os.path.isfile(path):
            raise FileNotFoundError(rel)
        if os.path.getsize(path) > self._max:
            raise ValueError("file too large to read")
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()

    def read_bytes(self, rel: str, *, max_bytes: int = 10_485_760) -> bytes:
        path = self.resolve(rel)
        if not os.path.isfile(path):
            raise FileNotFoundError(rel)
        if os.path.getsize(path) > max_bytes:
            raise ValueError("file too large to read")
        with open(path, "rb") as f:
            return f.read()

    def write_bytes(
        self, rel: str, data: bytes, *, max_bytes: int = 10_485_760
    ) -> str:
        if len(data) > max_bytes:
            raise ValueError(f"content too large (> {max_bytes} bytes)")
        self.ensure()
        path = self.resolve(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return os.path.relpath(path, os.path.realpath(self._root))

    def list(self, rel: str = ".") -> list[str]:
        path = self.resolve(rel)
        if not os.path.isdir(path):
            raise NotADirectoryError(rel)
        return [name + ("/" if os.path.isdir(os.path.join(path, name)) else "")
                for name in sorted(os.listdir(path))]

    def delete(self, rel: str) -> str:
        path = self.resolve(rel)
        if path == os.path.realpath(self._root):
            raise ValueError("cannot delete workspace root")
        if not os.path.isfile(path):
            raise FileNotFoundError(rel)
        os.remove(path)
        return os.path.relpath(path, os.path.realpath(self._root))


class Workspaces:
    """Builds a jailed UserWorkspace per user under a shared base dir."""

    def __init__(self, base_dir: str, *, max_file_bytes: int = 1_048_576):
        self._base, self._max = base_dir, max_file_bytes

    def for_user(self, user_id: str) -> UserWorkspace:
        return UserWorkspace(self._base, user_id, max_file_bytes=self._max)
