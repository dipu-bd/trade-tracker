from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TRADEBOT_",
        extra="ignore",
    )

    env: Literal["dev", "test", "prod"] = "dev"
    debug: bool = False

    database_url: str = "sqlite+aiosqlite:///./tradebot.db"
    database_echo: bool = False

    secret_key: str = Field(min_length=32)
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 30
    cookie_secure: bool = True
    cookie_domain: str | None = None

    cors_origins: list[str] = []
    static_dir: str | None = None

    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
