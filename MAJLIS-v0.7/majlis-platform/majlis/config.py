from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    bot_token: SecretStr
    database_url: SecretStr
    migration_database_url: SecretStr = SecretStr("")
    health_token: SecretStr = SecretStr("")
    public_requests_per_minute: int = 240
    environment: Literal["test", "production"] = "test"
    mini_app_url: str = ""
    manager_ids: str = ""
    payment_hosts: str = ""
    catalog_url: str = ""
    support_url: str = ""
    integration_secret: SecretStr = SecretStr("")
    port: int = 8876
    instance_id: str = ""
    https_mode: str = "localhost_run"
    auto_start_database: bool = True
    db_pool_size: int = 8

    @field_validator("bot_token")
    @classmethod
    def token_valid(cls, value):
        import re

        if not re.fullmatch(r"\d{5,15}:[A-Za-z0-9_-]{30,}", value.get_secret_value()):
            raise ValueError("Задайте локальный BOT_TOKEN в .env")
        return value

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def postgres_only(cls, value):
        from urllib.parse import urlsplit

        if not value.get_secret_value():
            return value
        if urlsplit(value.get_secret_value()).scheme not in ("postgresql", "postgres"):
            raise ValueError("Требуется PostgreSQL DATABASE_URL")
        return value

    @field_validator("manager_ids")
    @classmethod
    def valid_ids(cls, value):
        if any(
            not x.strip().isdigit() or int(x.strip()) <= 0 for x in value.split(",") if x.strip()
        ):
            raise ValueError("MANAGER_IDS: положительные Telegram ID через запятую")
        return value

    @field_validator("port", "db_pool_size", "public_requests_per_minute")
    @classmethod
    def positive(cls, value):
        if not 1 <= value <= 65535:
            raise ValueError("Настройка вне диапазона")
        return value

    @field_validator("https_mode")
    @classmethod
    def tunnel_mode(cls, value):
        if value not in ("localhost_run", "external"):
            raise ValueError("HTTPS_MODE: localhost_run или external")
        return value

    @property
    def managers(self):
        return {int(x.strip()) for x in self.manager_ids.split(",") if x.strip()}

    @property
    def payment_domains(self):
        return {x.strip().lower() for x in self.payment_hosts.split(",") if x.strip()}


@lru_cache
def settings():
    return Settings()
