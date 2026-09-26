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
    chat_timeout_seconds: int = 180
    # Secrets vault (filesystem MCP root ARI_FS_ROOT is read raw by the registry)
    vault_key: str = ""
    vault_path: str = "~/.ari/vault.enc"

    @property
    def owner_id_set(self) -> set[str]:
        return {p.strip() for p in self.owner_ids.split(",") if p.strip()}
