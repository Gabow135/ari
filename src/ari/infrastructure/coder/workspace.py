import asyncio
import os


class Workspace:
    def __init__(self, allowed_root: str):
        self._root = os.path.realpath(allowed_root)

    def resolve(self, target: str | None, default_dir: str) -> str:
        raw = default_dir if target is None or not target.strip() else target
        if not os.path.isabs(raw):
            raw = os.path.join(self._root, raw)
        path = os.path.realpath(raw)  # resolves symlinks + '..'
        if not self._inside_root(path):
            raise ValueError(f"resolved path '{path}' is outside allowed root '{self._root}'")
        if not os.path.isdir(path):
            raise ValueError(f"target directory does not exist: {path}")
        return path

    async def create_branch(self, target_dir: str, slug: str) -> str:
        real = os.path.realpath(target_dir)
        if not self._inside_root(real):
            raise ValueError(f"resolved path '{real}' is outside allowed root '{self._root}'")
        branch = f"ari/tg-{slug}"
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", target_dir, "checkout", "-b", branch,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git checkout -b failed: {err.decode(errors='replace')[:300]}")
        return branch

    def _inside_root(self, path: str) -> bool:
        # normcase: Windows paths are case-insensitive (D:\X == d:\x).
        p, root = os.path.normcase(path), os.path.normcase(self._root)
        return p == root or p.startswith(root + os.sep)
