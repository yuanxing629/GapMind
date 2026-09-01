"""Application configuration.

Loads from environment variables with sensible defaults for local dev.
The env file lives at the repo root (single source of truth shared with
docker compose and vite); resolve it from this file's location so the
CWD at launch time (backend/, repo root, IDE runner) does not matter.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
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
    workspace_storage_quota_bytes: int = 500 * 1024 * 1024

    # ---- Minimal delivery authentication ----
    # Format: comma-separated ``token:user_id`` pairs.  Development keeps the
    # historical single-user fallback; staging/production must use Bearer.
    auth_required: bool = False
    auth_tokens: str = ""

    # ---- Invitation + session authentication ----
    # In production this must be a random secret stored outside the repo.
    auth_session_secret: str = "development-only-change-me"
    auth_cookie_name: str = "gm_session"
    auth_csrf_cookie_name: str = "gm_csrf"
    auth_cookie_secure: bool = False
    auth_session_idle_hours: int = 12
    auth_session_max_days: int = 30
    auth_invite_ttl_hours: int = 72
    auth_password_reset_ttl_minutes: int = 30
    # 0 means no product-level password length limit. Deployments may set a
    # defensive request-size ceiling if their threat model requires it.
    auth_max_password_bytes: int = 0
    auth_login_rate_limit: int = 10
    auth_login_rate_window_seconds: int = 300

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
    # Only chat requests carrying images use this model. Keep text chat on the
    # regular model so the existing retrieval and cost profile is unchanged.
    deepseek_vision_model: str = "deepseek-v4-flash-vision-exp"
    # demo-day fuse: fall over to a backup OpenAI-compatible endpoint when the
    # primary fails; enabled only when all three backup fields are set
    deepseek_backup_api_key: str = ""
    deepseek_backup_base_url: str = ""
    deepseek_backup_model: str = ""

    # ---- Fine-tuned gap extractor (Ollama) ----
    gap_extractor_base_url: str = "http://127.0.0.1:11434"
    gap_extractor_model: str = "research-dataset-qwen3:run7-q8-templatefix"
    gap_extractor_model_digest: str = ""
    gap_extractor_timeout_seconds: float = 600.0
    gap_extractor_repair_attempts: int = 2
    gap_extractor_num_ctx: int = 32768
    gap_extractor_num_predict: int = 4096
    gap_extractor_temperature: float = 0.01
    gap_extractor_top_p: float = 1.0
    gap_extractor_repeat_penalty: float = 1.05
    gap_extractor_seed: int = 42
    # Remote gap extraction is controlled by this server-side feature flag and
    # the complete remote endpoint configuration. No remote endpoint is
    # configured by default; eligible local failures trigger it automatically.
    gap_extractor_remote_enabled: bool = False
    gap_extractor_remote_base_url: str = ""
    gap_extractor_remote_api_key: str = ""
    gap_extractor_remote_model: str = ""
    gap_extractor_remote_max_tokens: int = 4096

    # ---- Chat ----
    chat_history_message_limit: int = 20
    chat_history_char_limit: int = 60000
    chat_max_input_chars: int = 12000
    chat_max_image_count: int = 3
    chat_max_image_bytes: int = 8 * 1024 * 1024
    chat_prompt_max_context_chars: int = 48000
    chat_rag_top_k: int = 6
    chat_rag_max_context_chars: int = 18000
    chat_plan_max_context_chars: int = 6000
    chat_artifact_max_context_chars: int = 6000
    chat_workspace_profile_max_context_chars: int = 2000

    # Evidence Passport operational freshness policy. This describes the age
    # of the verification snapshot, not the scientific validity or publication
    # date of the cited paper. Keep the thresholds explicit and configurable so
    # a delivery environment can choose a stricter revalidation window.
    evidence_freshness_max_age_days: int = 30

    # ---- Controlled workspace agents ----
    agent_rag_top_k: int = 10
    agent_code_max_files: int = 30
    agent_code_max_chars: int = 300000

    # ---- Embedding (SiliconFlow, BGE-m3) ----
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dimension: int = 1024

    # ---- Knowledge extraction dedup ----
    # P1 semantic near-dup collapse (embedding cosine ≥ 0.9, same-paper + same-type
    # guard). Off by default; enabling it costs one embedding batch per paper.
    retrieval_dedup_semantic: bool = False

    # ---- Reranker (SiliconFlow, BGE-reranker-v2-m3) ----
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # ---- Semantic Scholar ----
    semantic_scholar_api_key: str = ""
    semantic_scholar_base_url: str = "https://api.semanticscholar.org/graph/v1"
    semantic_scholar_rate_interval: float = 1.1
    semantic_scholar_retry_count: int = 2
    semantic_scholar_retry_backoff: float = 1.5
    semantic_scholar_search_cache_ttl: int = 900

    # ---- CORS ----
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    @property
    def auth_is_configured(self) -> bool:
        """Whether the session flow has a usable non-empty secret."""
        secret = self.auth_session_secret.strip()
        if self.app_env == "development":
            return bool(secret)
        return len(secret) >= 32 and secret != "development-only-change-me" and self.auth_cookie_secure

    def validate_runtime_security(self) -> None:
        """Fail closed when a deployment has not supplied cookie secrets."""
        if self.app_env == "development":
            return
        if not self.auth_is_configured:
            raise RuntimeError(
                "Production/staging requires AUTH_SESSION_SECRET (32+ chars) and AUTH_COOKIE_SECURE=true"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
