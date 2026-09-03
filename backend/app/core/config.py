"""Application configuration.

Stable defaults live in ``config/gapmind.yaml``. Environment variables remain
the deployment override mechanism and take precedence over the YAML file.
The env files live at the repo root; resolving them from this file's location
keeps backend startup independent of the launch CWD.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, computed_field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CONFIG_FILE = _REPO_ROOT / "config" / "gapmind.yaml"


def _config_file_path() -> Path:
    """Resolve the YAML defaults file, optionally overridden by the process."""
    configured = os.getenv("GAPMIND_CONFIG_FILE", "").strip()
    if not configured:
        return _DEFAULT_CONFIG_FILE
    path = Path(configured).expanduser()
    return path if path.is_absolute() else _REPO_ROOT / path


def _flatten_yaml_sections(raw: Any) -> dict[str, Any]:
    """Map YAML sections such as ``chat.rag_top_k`` to Settings field names."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("GapMind YAML config must contain a mapping at the root")

    flattened: dict[str, Any] = {}
    for section, values in raw.items():
        if isinstance(values, dict):
            for key, value in values.items():
                flattened[f"{section}_{key}"] = value
        else:
            flattened[str(section)] = values
    return flattened


def _load_yaml_defaults() -> dict[str, Any]:
    """Load non-secret defaults from the repository configuration file."""
    config_path = _config_file_path()
    if not config_path.is_file():
        raise RuntimeError(f"GapMind config file not found: {config_path}")
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            return _flatten_yaml_sections(yaml.safe_load(handle))
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Invalid GapMind YAML config: {config_path}") from exc


class YamlSettingsSource(PydanticBaseSettingsSource):
    """Settings source for stable defaults stored in the YAML config."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._defaults = _load_yaml_defaults()

    def get_field_value(
        self,
        field: Any,
        field_name: str,
    ) -> tuple[Any, str, bool]:
        return self._defaults.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return self._defaults


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=(
            str(_REPO_ROOT / ".env"),
            str(_REPO_ROOT / ".env.local"),
        ),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Keep init/env/.env overrides ahead of YAML defaults."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlSettingsSource(settings_cls),
            file_secret_settings,
        )

    # ---- App ----
    app_env: Literal["development", "staging", "production"] = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_log_level: str = "INFO"
    app_storage_dir: str = "./storage"
    workspace_storage_quota_bytes: int = 500 * 1024 * 1024

    # ---- PDF parsing ----
    # Keep PyMuPDF as the safe default; local MinerU is opt-in while its
    # parsing quality is being validated on the project's paper set.
    parser_provider: Literal["pymupdf", "mineru_local"] = "pymupdf"
    parser_fallback_enabled: bool = True
    mineru_api_url: str = "http://127.0.0.1:8002"
    mineru_timeout_seconds: float = 1800.0
    # The original PDF is the reading source; derived paper images are opt-in.
    parser_return_images: bool = False

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

    # ---- OpenAI Chat Completions-compatible LLMs ----
    # The remote, vision, and backup endpoints may be supplied by different
    # providers. Secrets belong in .env/.env.local, not in the YAML defaults.
    remote_api_key: str = ""
    remote_base_url: str = "https://api.openai.com/v1"
    remote_model: str = ""
    vision_api_key: str = ""
    vision_base_url: str = ""
    vision_model: str = ""
    backup_api_key: str = ""
    backup_base_url: str = ""
    backup_model: str = ""

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
    # Gap extraction consumes the paper-local projection of knowledge
    # extraction by default. Legacy compact Markdown remains a rollout-safe
    # fallback until existing papers are backfilled.
    gap_extraction_context_mode: Literal[
        "knowledge_context_v1", "core_markdown_legacy_v1"
    ] = "knowledge_context_v1"
    gap_extraction_allow_legacy_markdown_fallback: bool = True
    gap_extraction_context_max_chars: int = 24000
    gap_extraction_require_knowledge: bool = False

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
