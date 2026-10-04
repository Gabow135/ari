import os


def claude_cli_env(oauth_token: str, config_dir: str) -> dict | None:
    """Environment that runs the claude CLI as Ari rather than as the host user.

    With Ari's own long-lived token (``claude setup-token``) and an empty config
    dir, the CLI has no ``oauthAccount``: it can't inject the host account's
    email into Ari's context, and Ari no longer shares (and races to refresh)
    the host's interactive login. Returns None (inherit the host login) when no
    token is configured.
    """
    if not oauth_token:
        return None
    config_dir = os.path.abspath(config_dir)
    os.makedirs(config_dir, exist_ok=True)
    env = {**os.environ,
           "CLAUDE_CODE_OAUTH_TOKEN": oauth_token,
           "CLAUDE_CONFIG_DIR": config_dir}
    env.pop("ANTHROPIC_API_KEY", None)  # must not override the subscription token
    return env


def with_mcp_startup_timeout(env: dict | None, seconds: int) -> dict:
    """Return ``env`` (or a copy of the host env) with ``MCP_TIMEOUT`` set.

    Ari cold-starts its MCP servers on every turn; without a bound, one server
    that hangs on startup (e.g. an IMAP host with no listener) burns the whole
    chat budget and Ari only ever replies the timeout backstop. ``MCP_TIMEOUT``
    is the claude CLI's per-server startup budget in milliseconds, so a dead
    server fails fast and the turn proceeds without it.
    """
    base = dict(env) if env is not None else dict(os.environ)
    base["MCP_TIMEOUT"] = str(int(seconds * 1000))
    return base
