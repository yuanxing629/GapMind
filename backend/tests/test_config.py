"""YAML 默认值和环境覆盖优先级测试。"""

from __future__ import annotations

from app.core.config import Settings, _flatten_yaml_sections


def test_yaml_defaults_are_loaded_without_dotenv_files() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.parser_provider == "pymupdf"
    assert settings.parser_return_images is False
    assert settings.mineru_api_url == "http://127.0.0.1:8002"
    assert settings.mineru_backend == "pipeline"
    assert settings.mineru_prefer_pymupdf_text is True
    assert settings.chat_rag_top_k == 6
    assert settings.embedding_dimension == 1024
    assert settings.remote_base_url == "https://api.openai.com/v1"
    assert settings.remote_model == ""
    assert settings.vision_model == ""
    assert settings.backup_model == ""
    assert settings.semantic_scholar_retry_count == 2
    assert settings.gap_extraction_context_mode == "knowledge_context_v1"
    assert settings.gap_extraction_allow_legacy_markdown_fallback is True
    assert settings.gap_extraction_context_max_chars == 24000


def test_environment_variables_override_yaml_defaults(monkeypatch) -> None:
    monkeypatch.setenv("CHAT_RAG_TOP_K", "11")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("PARSER_PROVIDER", "mineru_local")
    monkeypatch.setenv("MINERU_BACKEND", "hybrid-auto-engine")
    monkeypatch.setenv("MINERU_PREFER_PYMUPDF_TEXT", "false")
    monkeypatch.setenv("REMOTE_API_KEY", "remote-key")
    monkeypatch.setenv("REMOTE_BASE_URL", "https://remote.example/v1")
    monkeypatch.setenv("REMOTE_MODEL", "remote-model")
    monkeypatch.setenv("VISION_API_KEY", "vision-key")
    monkeypatch.setenv("VISION_BASE_URL", "https://vision.example/v1")
    monkeypatch.setenv("VISION_MODEL", "vision-model")
    monkeypatch.setenv("BACKUP_API_KEY", "backup-key")
    monkeypatch.setenv("BACKUP_BASE_URL", "https://backup.example/v1")
    monkeypatch.setenv("BACKUP_MODEL", "backup-model")

    settings = Settings(_env_file=None)

    assert settings.chat_rag_top_k == 11
    assert settings.auth_cookie_secure is True
    assert settings.parser_provider == "mineru_local"
    assert settings.mineru_backend == "hybrid-auto-engine"
    assert settings.mineru_prefer_pymupdf_text is False
    assert settings.remote_api_key == "remote-key"
    assert settings.remote_base_url == "https://remote.example/v1"
    assert settings.remote_model == "remote-model"
    assert settings.vision_api_key == "vision-key"
    assert settings.vision_base_url == "https://vision.example/v1"
    assert settings.vision_model == "vision-model"
    assert settings.backup_api_key == "backup-key"
    assert settings.backup_base_url == "https://backup.example/v1"
    assert settings.backup_model == "backup-model"


def test_yaml_sections_flatten_to_settings_field_names() -> None:
    assert _flatten_yaml_sections({"chat": {"rag_top_k": 7}, "flag": False}) == {
        "chat_rag_top_k": 7,
        "flag": False,
    }
