import asyncio
import json
import os

from ari.domain.ports.progress_port import TEXT, THINKING, TOOL, ProgressEvent, emit_progress

# Flags that make `claude -p` print one JSON event per line as it works.
STREAM_ARGS = ["--output-format", "stream-json", "--verbose", "--include-partial-messages"]
# Tool results arrive as single (possibly huge) lines; asyncio's default is 64 KiB.
_LINE_LIMIT = 32 * 1024 * 1024


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


async def run_streaming(argv: list[str], stdin: bytes | None = None,
                        cwd: str | None = None, timeout: float | None = None) -> str:
    """Run a stream-json `claude` command, emitting progress for each event.

    Returns the final ``result`` line, which has the same shape as the CLI's
    ``--output-format json`` output.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, limit=_LINE_LIMIT,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    async def consume() -> tuple[str | None, bytes]:
        stderr_task = asyncio.ensure_future(proc.stderr.read())
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
        err = await stderr_task
        await proc.wait()
        return result, err

    try:
        result, err = await asyncio.wait_for(consume(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"claude timed out after {timeout}s") from None
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed (exit {proc.returncode}): "
                           f"{err.decode(errors='replace')[:500]}")
    if result is None:
        raise RuntimeError("claude CLI produced no result")
    return result
