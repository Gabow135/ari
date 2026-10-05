import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ari.application.access.gate import AccessGate
from ari.application.ari_tools import AriTools
from ari.application.grants.grant_policy import GrantPolicy
from ari.application.skills.skill_manager import SkillManager
from ari.domain.tools.ari_permissions import allowed_ari_tools
from ari.infrastructure.access.sqlite_access_store import SqliteAccessStore
from ari.infrastructure.grants.sqlite_grant_store import SqliteGrantStore
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import open_existing
from ari.infrastructure.persistence.sqlite_coding_requests import SqliteCodingRequests
from ari.infrastructure.persistence.sqlite_command_requests import SqliteCommandRequests
from ari.infrastructure.persistence.sqlite_credential_requests import SqliteCredentialRequests
from ari.infrastructure.persistence.sqlite_missions import SqliteMissions
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.mcp_server.server import actor_from_env, build_server

_tools: AriTools | None = None


async def _get_tools() -> AriTools:
    """Built on the first tool call, so a bare handshake needs no actor env."""
    global _tools
    if _tools is None:
        from ari.infrastructure.command.shell_runner import ShellRunner
        from ari.infrastructure.email.sqlite_email_accounts import SqliteEmailAccounts
        from ari.infrastructure.persistence.sqlite_email_enroll_requests import (
            SqliteEmailEnrollRequests,
        )
        from ari.infrastructure.workspace.sqlite_sandbox import SqliteSandbox
        from ari.infrastructure.workspace.user_workspace import Workspaces
        env = os.environ
        conn = await open_existing(env["ARI_DB_PATH"])
        schedule, access = SqliteScheduleStore(conn), SqliteAccessStore(conn)
        grants = GrantPolicy(SqliteGrantStore(conn))
        owners = {o.strip() for o in env.get("ARI_OWNER_IDS", "").split(",") if o.strip()}
        email_accounts = SqliteEmailAccounts(conn, None)
        email_enroll = SqliteEmailEnrollRequests(conn)

        async def _on_revoke(user_id: str) -> None:
            await schedule.cancel_user(user_id)
            await grants.forget_user(user_id)
            await email_accounts.delete_for_user(user_id)

        workspaces = Workspaces(env.get(
            "ARI_WORKSPACES_DIR", os.path.expanduser("~/.ari/workspaces")))
        from ari.infrastructure.persistence.sqlite_file_requests import SqliteFileRequests
        from ari.infrastructure.vault_web.fs_denylist import default_denied_roots
        denied_roots = default_denied_roots(
            env.get("ARI_VAULT_PATH", "~/.ari/vault.enc"),
            env.get("ARI_CLAUDE_CONFIG_DIR", "./.ari-claude"))
        _tools = AriTools(
            actor_from_env(env), schedule=schedule,
            memory=SqliteMemoryAdapter(conn, embedding_dim=1),
            turn_log=SqliteTurnLog(conn), tz=ZoneInfo(env.get("ARI_TIMEZONE", "UTC")),
            max_items=int(env.get("ARI_MAX_ITEMS", "20")),
            clock=lambda: datetime.now(UTC),
            gate=AccessGate(access, owners, on_revoke=_on_revoke), access=access,
            coding=SqliteCodingRequests(conn),
            commands=SqliteCommandRequests(conn),
            missions=SqliteMissions(conn),
            credentials=SqliteCredentialRequests(conn),
            skills=SkillManager(env.get("ARI_SKILLS_DIR", "./skills"), load=False),
            grants=grants,
            email_accounts=email_accounts,
            email_enroll=email_enroll,
            workspaces=workspaces,
            sql_sandbox=SqliteSandbox(),
            runner=ShellRunner(timeout=60.0),
            files=SqliteFileRequests(conn),
            denied_roots=denied_roots)
    return _tools


def main() -> None:
    role, context = os.environ.get("ARI_ROLE"), os.environ.get("ARI_CONTEXT")
    # A bare handshake (no actor env yet) gets the full catalogue; once Ari's
    # per-turn env is set, only the tools that role/context allow are exposed.
    allowed = allowed_ari_tools(role == "owner", context) if role and context else None
    build_server(_get_tools, allowed).run()


if __name__ == "__main__":
    main()
