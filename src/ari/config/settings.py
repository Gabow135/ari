from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARI_", env_file=".env",
                                      extra="ignore")

    anthropic_api_key: str = Field(
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "ARI_ANTHROPIC_API_KEY"))
    telegram_bot_token: str = Field(
        validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "ARI_TELEGRAM_BOT_TOKEN"))
    model: str = "claude-sonnet-4-6"
    db_path: str = "./ari.db"
    working_memory_size: int = 20
    recall_top_k: int = 5
    embedding_model: str = "intfloat/multilingual-e5-large"
