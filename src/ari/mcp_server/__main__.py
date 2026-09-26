import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ari.application.access.gate import AccessGate
from ari.application.ari_tools import AriTools
from ari.infrastructure.access.sqlite_access_store import SqliteAccessStore
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import open_existing
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.domain.tools.ari_permissions import allowed_ari_tools
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.mcp_server.server import actor_from_env, build_server

_tools: AriTools | None = None


async def _get_tools() -> AriTools:
    """Built on the first tool call, so a bare handshake needs no actor env."""
    global _tools
    if _tools is None:
        env = os.environ
        conn = await open_existing(env["ARI_DB_PATH"])
        schedule, access = SqliteScheduleStore(conn), SqliteAccessStore(conn)
        owners = {o.strip() for o in env.get("ARI_OWNER_IDS", "").split(",") if o.strip()}
        _tools = AriTools(
            actor_from_env(env), schedule=schedule,
            memory=SqliteMemoryAdapter(conn, embedding_dim=1),  # facts only, no vectors
            turn_log=SqliteTurnLog(conn), tz=ZoneInfo(env.get("ARI_TIMEZONE", "UTC")),
            max_items=int(env.get("ARI_MAX_ITEMS", "20")),
            clock=lambda: datetime.now(timezone.utc),
            gate=AccessGate(access, owners, on_revoke=schedule.cancel_user), access=access)
    return _tools


def main() -> None:
    role, context = os.environ.get("ARI_ROLE"), os.environ.get("ARI_CONTEXT")
    # A bare handshake (no actor env yet) gets the full catalogue; once Ari's
    # per-turn env is set, only the tools that role/context allow are exposed.
    allowed = allowed_ari_tools(role == "owner", context) if role and context else None
    build_server(_get_tools, allowed).run()


if __name__ == "__main__":
    main()
