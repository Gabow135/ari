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
