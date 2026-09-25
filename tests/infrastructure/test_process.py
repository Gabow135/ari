import sys

from ari.infrastructure.process import RESTART_NOTIFY_ENV, relaunch


def test_posix_replaces_process_with_notify_env():
    calls = []
    relaunch("42", platform="posix", execve=lambda *a: calls.append(a),
             popen=lambda *a, **k: calls.append(("popen", a)))
    [(exe, argv, env)] = calls
    assert exe == sys.executable
    assert argv[:3] == [sys.executable, "-m", "ari.main"]
    assert env[RESTART_NOTIFY_ENV] == "42"


def test_windows_spawns_new_process_with_notify_env():
    calls = []
    relaunch("42", platform="nt", execve=lambda *a: calls.append(("execve", a)),
             popen=lambda argv, env: calls.append((argv, env)))
    [(argv, env)] = calls
    assert argv[:3] == [sys.executable, "-m", "ari.main"]
    assert env[RESTART_NOTIFY_ENV] == "42"
