from __future__ import annotations

from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    llm_provider: Literal["fake", "openai", "gigachat", "deepseek"] = "fake"
    # observe: only for initial real-provider verification; production: required.
    llm_self_check_mode: Literal["observe", "required"] = "required"
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    openai_timeout_seconds: float = 30.0
    openai_max_retries: int = 2

    gigachat_credentials: SecretStr | None = None
    gigachat_model: str | None = None
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_base_url: str = "https://api.giga.chat/v1"
    gigachat_auth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    gigachat_timeout_seconds: float = 30.0
    gigachat_ca_bundle: str | None = None

    deepseek_api_key: SecretStr | None = None
    deepseek_model: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_timeout_seconds: float = 60.0
    deepseek_max_retries: int = 2

    relationship_backend: Literal["memory", "postgres"] = "memory"
    database_url: str | None = None

    checkpoint_backend: Literal["memory", "redis"] = "memory"
    redis_url: str | None = None
    redis_checkpoint_ttl_seconds: int = 1200

    telegram_enabled: bool = False
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_webhook_url: str | None = None
    telegram_default_workflow: Literal["soften", "decode", "help-say"] = "soften"
    telegram_init_data_max_age_seconds: int = 3600

    miniapp_static_dir: str = "frontend/dist"

    @model_validator(mode="after")
    def validate_production_dependencies(self) -> "AppSettings":
        if self.llm_provider == "openai":
            if self.openai_api_key is None:
                raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
            if not self.openai_model:
                raise ValueError("OPENAI_MODEL is required when LLM_PROVIDER=openai")
        if self.llm_provider == "gigachat":
            if self.gigachat_credentials is None:
                raise ValueError("GIGACHAT_CREDENTIALS is required when LLM_PROVIDER=gigachat")
            if not self.gigachat_model:
                raise ValueError("GIGACHAT_MODEL is required when LLM_PROVIDER=gigachat")
        if self.llm_provider == "deepseek":
            if self.deepseek_api_key is None:
                raise ValueError("DEEPSEEK_API_KEY is required when LLM_PROVIDER=deepseek")
            if not self.deepseek_model:
                raise ValueError("DEEPSEEK_MODEL is required when LLM_PROVIDER=deepseek")
        if self.relationship_backend == "postgres" and not self.database_url:
            raise ValueError("DATABASE_URL is required when RELATIONSHIP_BACKEND=postgres")
        if self.checkpoint_backend == "redis" and not self.redis_url:
            raise ValueError("REDIS_URL is required when CHECKPOINT_BACKEND=redis")
        if self.telegram_enabled and self.telegram_bot_token is None:
            raise ValueError("TELEGRAM_BOT_TOKEN is required when TELEGRAM_ENABLED=true")
        if self.app_env == "production" and self.telegram_enabled and self.telegram_webhook_secret is None:
            raise ValueError("TELEGRAM_WEBHOOK_SECRET is required for Telegram in production")
        return self
