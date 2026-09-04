"""应用配置。

稳定默认值位于 ``config/gapmind.yaml``。环境变量仍然是部署覆盖机制，并优先于 YAML 文件。
环境文件位于仓库根目录；从本文件位置解析它们，使后端启动不依赖启动时的 CWD。
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
    """解析 YAML 默认配置文件，可由进程配置覆盖。"""
    configured = os.getenv("GAPMIND_CONFIG_FILE", "").strip()
    if not configured:
        return _DEFAULT_CONFIG_FILE
    path = Path(configured).expanduser()
    return path if path.is_absolute() else _REPO_ROOT / path


def _flatten_yaml_sections(raw: Any) -> dict[str, Any]:
    """将 ``chat.rag_top_k`` 等 YAML 节映射到 Settings 字段名。"""
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
    """从仓库配置文件加载非敏感默认值。"""
    config_path = _config_file_path()
    if not config_path.is_file():
        raise RuntimeError(f"GapMind config file not found: {config_path}")
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            return _flatten_yaml_sections(yaml.safe_load(handle))
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Invalid GapMind YAML config: {config_path}") from exc


class YamlSettingsSource(PydanticBaseSettingsSource):
    """存储 YAML 配置稳定默认值的 Settings 来源。"""

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
    """从环境变量加载的应用设置。"""

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
        """让 init/env/.env 覆盖优先于 YAML 默认值。"""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlSettingsSource(settings_cls),
            file_secret_settings,
        )

# ---- 应用 ----
    app_env: Literal["development", "staging", "production"] = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_log_level: str = "INFO"
    app_storage_dir: str = "./storage"
    workspace_storage_quota_bytes: int = 500 * 1024 * 1024

# ---- PDF 解析 ----
# 保持 PyMuPDF 作为安全默认值；本地 MinerU 需显式启用，当前仍在项目论文集上
# 验证其解析质量。
    parser_provider: Literal["pymupdf", "mineru_local"] = "pymupdf"
    parser_fallback_enabled: bool = True
    mineru_api_url: str = "http://127.0.0.1:8002"
    mineru_timeout_seconds: float = 1800.0
# 原始 PDF 是阅读来源；派生论文图片需显式启用。
    parser_return_images: bool = False

# ---- 最小交付认证 ----
# 格式：逗号分隔的 ``token:user_id`` 对。开发环境保留历史单用户 fallback；
# staging/production 必须使用 Bearer。
    auth_required: bool = False
    auth_tokens: str = ""

# ---- 邀请与会话认证 ----
# 生产环境必须使用存储在仓库外部的随机 secret。
    auth_session_secret: str = "development-only-change-me"
    auth_cookie_name: str = "gm_session"
    auth_csrf_cookie_name: str = "gm_csrf"
    auth_cookie_secure: bool = False
    auth_session_idle_hours: int = 12
    auth_session_max_days: int = 30
    auth_invite_ttl_hours: int = 72
    auth_password_reset_ttl_minutes: int = 30
# 0 表示不设置产品级密码长度限制。部署环境可按自身威胁模型设置防御性的请求大小上限。
    auth_max_password_bytes: int = 0
    auth_login_rate_limit: int = 10
    auth_login_rate_window_seconds: int = 300

# ---- PostgreSQL：数据库 ----
    postgres_user: str = "gapmind"
    postgres_password: str = "gapmind"
    postgres_db: str = "gapmind"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    database_url: str = Field(
        default="postgresql+psycopg://gapmind:gapmind@localhost:5432/gapmind",
        description="Sync DB URL for SQLAlchemy + Alembic.",
    )

# ---- Redis / Celery：任务队列 ----
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

# ---- Milvus：向量库 ----
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection_prefix: str = "gapmind_"

# ---- 兼容 OpenAI Chat Completions 的 LLM ----
# remote、vision 和 backup 端点可以由不同 provider 提供。secret 应放在
# .env/.env.local 中，不应写入 YAML 默认值。
    remote_api_key: str = ""
    remote_base_url: str = "https://api.openai.com/v1"
    remote_model: str = ""
    vision_api_key: str = ""
    vision_base_url: str = ""
    vision_model: str = ""
    backup_api_key: str = ""
    backup_base_url: str = ""
    backup_model: str = ""

# ---- 微调 gap extractor（Ollama） ----
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
# remote gap extraction 由服务端 feature flag 和完整的 remote 端点配置共同控制。
# 默认不配置 remote 端点；符合条件的本地失败会自动触发它。
    gap_extractor_remote_enabled: bool = False
    gap_extractor_remote_base_url: str = ""
    gap_extractor_remote_api_key: str = ""
    gap_extractor_remote_model: str = ""
    gap_extractor_remote_max_tokens: int = 4096
# gap extraction 默认使用论文局部的 knowledge extraction 投影。在现有论文完成
# backfill 前，legacy compact Markdown 仍作为安全的 rollout fallback。
    gap_extraction_context_mode: Literal[
        "knowledge_context_v1", "core_markdown_legacy_v1"
    ] = "knowledge_context_v1"
    gap_extraction_allow_legacy_markdown_fallback: bool = True
    gap_extraction_context_max_chars: int = 24000
    gap_extraction_require_knowledge: bool = False

# ---- Chat：对话 ----
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
    # 本阶段 PostgreSQL-first GraphRAG 仅用于诊断，默认以 shadow 模式运行；
    # dense evidence 仍然作为回答上下文。
    chat_graphrag_shadow_enabled: bool = True
    chat_graphrag_projection_version: str = "sql_graph_v1"
    chat_graphrag_max_hops: int = 2
    chat_graphrag_node_limit: int = 32
    chat_graphrag_edge_limit: int = 64
    chat_graphrag_timeout_ms: int = 250

# Evidence Passport 的运行时新鲜度策略。这里描述的是验证快照的时效，不是科学
# 有效性或被引用论文的发表日期。保持阈值明确且可配置，以便交付环境选择更严格
# 的重新验证窗口。
    evidence_freshness_max_age_days: int = 30

# ---- 受控 workspace agents ----
    agent_rag_top_k: int = 10
    agent_code_max_files: int = 30
    agent_code_max_chars: int = 300000

# ---- Embedding（SiliconFlow、BGE-m3）：向量生成 ----
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dimension: int = 1024

# ---- Knowledge extraction 去重 ----
# P1 语义近重复折叠（embedding cosine ≥ 0.9，并增加 same-paper + same-type
# 保护）。默认关闭；启用后每篇论文会额外消耗一个 embedding batch。
    retrieval_dedup_semantic: bool = False

# ---- Reranker（SiliconFlow、BGE-reranker-v2-m3）：重排 ----
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

# ---- Semantic Scholar：外部论文检索 ----
    semantic_scholar_api_key: str = ""
    semantic_scholar_base_url: str = "https://api.semanticscholar.org/graph/v1"
    semantic_scholar_rate_interval: float = 1.1
    semantic_scholar_retry_count: int = 2
    semantic_scholar_retry_backoff: float = 1.5
    semantic_scholar_search_cache_ttl: int = 900

# ---- CORS：跨域资源共享 ----
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
        """判断 session 流程是否具有可用的非空 secret。"""
        secret = self.auth_session_secret.strip()
        if self.app_env == "development":
            return bool(secret)
        return len(secret) >= 32 and secret != "development-only-change-me" and self.auth_cookie_secure

    def validate_runtime_security(self) -> None:
        """部署未提供 cookie secret 时采用 fail-closed。"""
        if self.app_env == "development":
            return
        if not self.auth_is_configured:
            raise RuntimeError(
                "Production/staging requires AUTH_SESSION_SECRET (32+ chars) and AUTH_COOKIE_SECURE=true"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """缓存 Settings 的访问器。"""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
