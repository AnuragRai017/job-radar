from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment variables / .env files."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "RoleRadar API"
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./data/jobs.db"
    db_echo: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = 5200
    cors_origins: str = (
        "http://localhost:5100,http://127.0.0.1:5100,"
        "http://localhost:4321,http://127.0.0.1:4321"
    )
    page_size_default: int = 20
    page_size_max: int = 100

    # Scheduling
    scrape_sources: str = (
        "amazon,concentrix,microsoft,hsbc,medallion,cadence,"
        "capgemini_india,exl,deloitte_india,ey,"
        "jpmorgan,deloitte,capgemini,google"
    )
    scrape_interval_hours: float = 6.0
    scrape_on_startup: bool = True
    scrape_max_jobs_per_source: int = 0
    scrape_max_pages_per_source: int = 0

    # HTTP / anti-bot
    http_timeout_seconds: float = 60.0
    http_max_retries: int = 4
    http_rate_limit_seconds: float = 1.5
    http_backoff_base_seconds: float = 2.0
    http_backoff_max_seconds: float = 30.0
    respect_robots: bool = True
    robots_cache_seconds: int = 86_400
    google_pagination_override: bool = False
    enable_driverless_fallback: bool = False
    driverless_headless: bool = True

    # Optional Ollama AI extraction fallback
    ai_fallback_enabled: bool = False
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "gpt-oss:120b-cloud"
    ollama_timeout_seconds: float = 120.0
    ollama_num_ctx: int = 32_768
    ai_fallback_max_calls: int = 10
    ai_fallback_max_input_chars: int = 60_000

    @field_validator("scrape_interval_hours")
    @classmethod
    def _positive_interval(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("SCRAPE_INTERVAL_HOURS must be greater than zero")
        return value

    @field_validator("http_rate_limit_seconds", "http_timeout_seconds")
    @classmethod
    def _positive_http_values(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("HTTP timing values must be greater than zero")
        return value

    @field_validator("ollama_timeout_seconds", "ai_fallback_max_input_chars")
    @classmethod
    def _positive_ai_values(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("AI timeout and input-size values must be greater than zero")
        return value

    @field_validator("ollama_num_ctx", "ai_fallback_max_calls")
    @classmethod
    def _positive_ai_counts(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("AI context and call limits must be greater than zero")
        return value

    @field_validator("ollama_model")
    @classmethod
    def _nonempty_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("OLLAMA_MODEL must not be empty")
        return value

    @field_validator("ollama_base_url")
    @classmethod
    def _valid_ollama_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("OLLAMA_BASE_URL must start with http:// or https://")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def enabled_sources(self) -> list[str]:
        return [name.strip().lower() for name in self.scrape_sources.split(",") if name.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def app_dir(self) -> Path:
        return BASE_DIR


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
