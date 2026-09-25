import json
import os
import sys

import pytest

from ari.domain.ports.progress_port import TEXT, THINKING, TOOL, current_progress
from ari.infrastructure.llm.stream_json import parse_line, run_streaming


def _line(obj) -> str:
    return json.dumps(obj)


def test_thinking_block_start_is_thinking_event():
    ev, res = parse_line(_line({"type": "stream_event", "event": {
        "type": "content_block_start", "content_block": {"type": "thinking"}}}))
    assert ev.kind == THINKING and res is None


def test_text_delta_is_text_event():
    ev, _ = parse_line(_line({"type": "stream_event", "event": {
        "type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hola"}}}))
    assert (ev.kind, ev.detail) == (TEXT, "Hola")


@pytest.mark.parametrize("name,inp,detail", [
    ("Read", {"file_path": "D:/Proyectos/ari/README.md"}, "README.md"),
    ("Edit", {"file_path": "/src/ari/main.py", "old_string": "x"}, "main.py"),
    ("Bash", {"command": "python -m pytest -q"}, "python -m pytest -q"),
    ("Grep", {"pattern": "def main"}, "def main"),
    ("Mystery", {}, ""),
])
def test_assistant_tool_use_is_tool_event(name, inp, detail):
    ev, _ = parse_line(_line({"type": "assistant", "message": {"content": [
        {"type": "thinking", "thinking": ""},
        {"type": "tool_use", "name": name, "input": inp}]}}))
    assert (ev.kind, ev.name, ev.detail) == (TOOL, name, detail)


def test_result_line_is_returned_raw():
    raw = _line({"type": "result", "is_error": False, "result": "pong"})
    ev, res = parse_line(raw)
    assert ev is None and json.loads(res)["result"] == "pong"


@pytest.mark.parametrize("raw", ["", "not json", _line({"type": "system", "subtype": "init"}),
                                 _line({"type": "stream_event", "event": {
                                     "type": "content_block_delta",
                                     "delta": {"type": "signature_delta"}}})])
def test_irrelevant_lines_are_ignored(raw):
    assert parse_line(raw) == (None, None)


class _Sink:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


_FAKE_CLI = r"""
import json, sys
data = sys.stdin.read()
print(json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
      "delta": {"type": "text_delta", "text": data}}}))
print(json.dumps({"type": "user", "blob": "x" * 200000}))  # > asyncio's 64 KiB default
print(json.dumps({"type": "result", "is_error": False, "result": "done"}))
"""


async def test_run_streaming_emits_events_and_returns_result():
    sink = _Sink()
    token = current_progress.set(sink)
    try:
        raw = await run_streaming([sys.executable, "-c", _FAKE_CLI], stdin=b"hi")
    finally:
        current_progress.reset(token)
    assert json.loads(raw)["result"] == "done"
    assert [(e.kind, e.detail) for e in sink.events] == [(TEXT, "hi")]


async def test_run_streaming_nonzero_exit_raises():
    with pytest.raises(RuntimeError, match="exit 3"):
        await run_streaming([sys.executable, "-c", "import sys; sys.exit(3)"])


async def test_run_streaming_timeout_kills_process():
    with pytest.raises(RuntimeError, match="timed out"):
        await run_streaming([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)


async def test_run_streaming_passes_env_to_cli():
    script = ("import json, os; print(json.dumps({'type': 'result', 'is_error': False, "
              "'result': os.environ.get('ARI_PROBE', '')}))")
    env = {**os.environ, "ARI_PROBE": "visto"}
    raw = await run_streaming([sys.executable, "-c", script], env=env)
    assert json.loads(raw)["result"] == "visto"
