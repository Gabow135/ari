from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARI_", env_file=".env",
                                      extra="ignore")

    telegram_bot_token: str = Field(
        validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "ARI_TELEGRAM_BOT_TOKEN"))
    model: str = "claude-sonnet-4-6"
    db_path: str = "./ari.db"
    working_memory_size: int = 20
    recall_top_k: int = 5
    # Single-file ONNX multilingual model — avoids the onnxruntime "external data
    # path escapes model directory" error that large split models (e.g.
    # intfloat/multilingual-e5-large, which ships model.onnx + model.onnx_data)
    # hit with the HuggingFace blob cache.
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    claude_bin: str = "claude"
    owner_ids: str = ""
    allowed_root: str = "."
    coder_model: str = "claude-sonnet-4-6"
    coding_timeout_seconds: int = 900
    soul_dir: str = "./soul"
    # Ari's own CLI login (`claude setup-token`); keeps the host account out of Ari.
    claude_oauth_token: str = ""
    claude_config_dir: str = "./.ari-claude"
    # Proactivity
    timezone: str = "America/Guayaquil"
    quiet_hours: str = "22-7"  # local hours "start-end"; empty = none
    heartbeat_minutes: int = 60  # 0 disables the heartbeat
    max_items_per_user: int = 20
    # Tools / MCP (3A)
    mcp_config: str = "./mcp/servers.json"
    skills_dir: str = "./skills"
    # Generous budget so slow MCP tools finish and Ari replies with the real
    # answer; the backstop only fires if a tool genuinely hangs (TIMEOUT_REPLY).
    chat_timeout_seconds: int = 900
    # Per-server MCP startup budget (seconds). A dead server (e.g. an IMAP host
    # with no listener) fails fast instead of burning the whole chat budget.
    mcp_startup_timeout_seconds: int = 20
    # Concurrency (L1/L2): chat is capped by PTB's concurrent_updates; background
    # agents (missions, scheduled tasks, coding) share a separate bounded pool so
    # a flood never starves the interactive chat nor thrashes the host.
    max_concurrent_chats: int = 8
    max_background_agents: int = 3
    # Logging: persistent rotating file so runtime behavior (chat-turn timeouts
    # above all) can be followed after the fact. Empty log_file = console only.
    log_file: str = "./logs/ari.log"
    log_level: str = "INFO"
    # Secrets vault (filesystem MCP root ARI_FS_ROOT is read raw by the registry)
    vault_key: str = ""
    vault_path: str = "~/.ari/vault.enc"
    # Vault web maintainer (LAN HTTPS link to load secrets)
    vault_web_port: int = 8765
    vault_web_ttl_minutes: int = 10
    vault_web_bind: str = "0.0.0.0"
    # Recall precision (Frente 1)
    candidate_multiplier: int = 4
    dedup_similarity: float = 0.98
    rank_min_similarity: float = 0.3
    rank_similarity_weight: float = 0.6
    rank_recency_weight: float = 0.25
    rank_importance_weight: float = 0.15
    rank_recency_half_life_days: float = 30.0
    # Importance + decay (Frente 3)
    reinforce_delta: float = 0.1
    decay_factor: float = 0.9
    prune_floor: float = 0.15
    prune_min_age_days: int = 7
    consolidate_interval_hours: int = 6

    @property
    def owner_id_set(self) -> set[str]:
        return {p.strip() for p in self.owner_ids.split(",") if p.strip()}
