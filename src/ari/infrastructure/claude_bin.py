import logging
import os
import shutil

log = logging.getLogger("ari.claude_bin")

# npm installs `claude` on Windows as a `claude.cmd` shim that forwards to this exe.
_NPM_EXE = os.path.join("node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")


def resolve_claude_bin(name: str) -> str:
    """Return a path to the Claude Code CLI that ``create_subprocess_exec`` can run.

    On Windows, CreateProcess does not search PATHEXT, so a bare ``claude`` is not
    found; and running the npm ``claude.cmd`` shim routes arguments through
    cmd.exe, which mangles multi-line prompts. Prefer the real ``claude.exe``.
    """
    found = shutil.which(name)
    if found is None:
        return name
    if os.name == "nt" and found.lower().endswith((".cmd", ".bat")):
        exe = os.path.join(os.path.dirname(found), _NPM_EXE)
        if os.path.isfile(exe):
            return exe
        log.warning("using batch shim %s; multi-line arguments may break", found)
    return found
