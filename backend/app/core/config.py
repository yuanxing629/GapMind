"""Application configuration.

Loads from environment variables with sensible defaults for local dev.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ----
    app_env: Literal["development", "staging", "production"] = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_log_level: str = "INFO"
    app_storage_dir: str = "./storage"

    # ---- PostgreSQL ----
    postgres_user: str = "gapmind"
    postgres_password: str = "gapmind"
    postgres_db: str = "gapmind"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    database_url: str = Field(
        default="postgresql+psycopg://gapmind:gapmind@localhost:5432/gapmind",
        description="Sync DB URL for SQLAlchemy + Alembic.",
    )

    # ---- Redis / Celery ----
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ---- Milvus ----
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection_prefix: str = "gapmind_"

    # ---- LLM (Deepseek) ----
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"

    # ---- Embedding (SiliconFlow, BGE-m3) ----
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dimension: int = 1024

    # ---- Semantic Scholar ----
    semantic_scholar_api_key: str = ""
    semantic_scholar_base_url: str = "https://api.semanticscholar.org/graph/v1"

    # ---- CORS ----
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
