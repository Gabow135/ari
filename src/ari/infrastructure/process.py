import os
import subprocess
import sys

# Set on the relaunched process: the chat to tell "I'm back" once it is up.
RESTART_NOTIFY_ENV = "ARI_RESTART_NOTIFY"


def relaunch(notify_chat_id: str, platform: str = os.name,
             execve=os.execve, popen=subprocess.Popen) -> None:
    """Start a fresh Ari process (picking up code changes).

    POSIX replaces the current process in place (same PID, supervisor-friendly).
    Windows has no true exec, so it spawns a new process in the same console;
    the caller then simply returns and this process exits.
    """
    argv = [sys.executable, "-m", "ari.main", *sys.argv[1:]]
    env = {**os.environ, RESTART_NOTIFY_ENV: notify_chat_id}
    if platform == "nt":
        popen(argv, env=env)
    else:
        execve(sys.executable, argv, env)
