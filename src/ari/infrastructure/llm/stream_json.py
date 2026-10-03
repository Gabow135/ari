import asyncio
import json
import logging
import os

from ari.domain.ports.llm_port import LLMTimeoutError
from ari.domain.ports.progress_port import TEXT, THINKING, TOOL, ProgressEvent, emit_progress

log = logging.getLogger("ari.llm.stream")

# Flags that make `claude -p` print one JSON event per line as it works.
STREAM_ARGS = ["--output-format", "stream-json", "--verbose", "--include-partial-messages"]
# Tool results arrive as single (possibly huge) lines; asyncio's default is 64 KiB.
_LINE_LIMIT = 32 * 1024 * 1024
# How much of the CLI's stderr to log when a turn times out — enough to show the
# MCP server that hung without dumping the whole stream.
_STDERR_TAIL = 2000


def _tool_detail(inp: dict) -> str:
    if inp.get("file_path"):
        return os.path.basename(str(inp["file_path"]).replace("\\", "/"))
    for key in ("command", "pattern", "path", "url", "query"):
        if inp.get(key):
            return str(inp[key]).splitlines()[0][:80]
    return ""


def parse_line(line: str) -> tuple[ProgressEvent | None, str | None]:
    """Map one stream-json line to (progress event, raw final result line)."""
    try:
        data = json.loads(line)
    except (ValueError, TypeError):
        return None, None
    kind = data.get("type")
    if kind == "result":
        return None, line
    if kind == "stream_event":
        ev = data.get("event") or {}
        if (ev.get("type") == "content_block_start"
                and (ev.get("content_block") or {}).get("type") == "thinking"):
            return ProgressEvent(THINKING), None
        delta = ev.get("delta") or {}
        if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            return ProgressEvent(TEXT, detail=delta.get("text", "")), None
    if kind == "assistant":
        for block in (data.get("message") or {}).get("content") or []:
            if block.get("type") == "tool_use":
                return ProgressEvent(TOOL, block.get("name", ""),
                                     _tool_detail(block.get("input") or {})), None
    return None, None


async def _collect_stderr(task: "asyncio.Future[bytes]") -> bytes:
    """Await the stderr-drain task, tolerating a kill that interrupts the read."""
    try:
        return await task
    except Exception:  # noqa: BLE001 — stderr is best-effort diagnostics
        task.cancel()
        return b""


async def run_streaming(argv: list[str], stdin: bytes | None = None,
                        cwd: str | None = None, timeout: float | None = None,
                        env: dict | None = None) -> str:
    """Run a stream-json `claude` command, emitting progress for each event.

    Returns the final ``result`` line, which has the same shape as the CLI's
    ``--output-format json`` output.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=env, limit=_LINE_LIMIT,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    # Drain stderr concurrently so a chatty CLI never blocks on a full pipe, and
    # so the tail survives a timeout kill (that's where a hung MCP server speaks).
    stderr_task = asyncio.ensure_future(proc.stderr.read())

    async def consume() -> str | None:
        if stdin is not None:
            proc.stdin.write(stdin)
            await proc.stdin.drain()
            proc.stdin.close()
        result = None
        async for raw in proc.stdout:
            event, res = parse_line(raw.decode(errors="replace"))
            if event is not None:
                emit_progress(event)
            if res is not None:
                result = res
        return result

    try:
        result = await asyncio.wait_for(consume(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        err = await _collect_stderr(stderr_task)
        tail = err.decode(errors="replace").strip()
        if tail:
            log.warning("claude timed out after %ss; CLI stderr tail:\n%s",
                        timeout, tail[-_STDERR_TAIL:])
        else:
            log.warning("claude timed out after %ss (no CLI stderr)", timeout)
        raise LLMTimeoutError(f"claude timed out after {timeout}s") from None
    err = await stderr_task
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed (exit {proc.returncode}): "
                           f"{err.decode(errors='replace')[:500]}")
    if result is None:
        raise RuntimeError("claude CLI produced no result")
    return result
