"""Tests for YAML defaults and environment override precedence."""

from __future__ import annotations

from app.core.config import Settings, _flatten_yaml_sections


def test_yaml_defaults_are_loaded_without_dotenv_files() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.parser_provider == "pymupdf"
    assert settings.parser_return_images is False
    assert settings.mineru_api_url == "http://127.0.0.1:8002"
    assert settings.chat_rag_top_k == 6
    assert settings.embedding_dimension == 1024
    assert settings.semantic_scholar_retry_count == 2
    assert settings.gap_extraction_context_mode == "knowledge_context_v1"
    assert settings.gap_extraction_allow_legacy_markdown_fallback is True
    assert settings.gap_extraction_context_max_chars == 24000


def test_environment_variables_override_yaml_defaults(monkeypatch) -> None:
    monkeypatch.setenv("CHAT_RAG_TOP_K", "11")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("PARSER_PROVIDER", "mineru_local")

    settings = Settings(_env_file=None)

    assert settings.chat_rag_top_k == 11
    assert settings.auth_cookie_secure is True
    assert settings.parser_provider == "mineru_local"


def test_yaml_sections_flatten_to_settings_field_names() -> None:
    assert _flatten_yaml_sections({"chat": {"rag_top_k": 7}, "flag": False}) == {
        "chat_rag_top_k": 7,
        "flag": False,
    }
