from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/task_manager"
    jwt_secret: SecretStr = Field(min_length=32)
    access_token_minutes: int = Field(default=15, gt=0)
    refresh_token_days: int = Field(default=7, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
