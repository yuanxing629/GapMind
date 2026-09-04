"""判断网关单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

from app.gateway.judge import JudgementGateway


def _gateway_with_response(content: str, finish_reason: str = "stop"):
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content),
                    finish_reason=finish_reason,
                )
            ]
        )

    gateway = JudgementGateway(api_key="test")
    gateway._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create),
        )
    )
    return gateway, calls


def test_batch_judgement_uses_standard_chat_completion_fields() -> None:
    content = (
        "["
        + ",".join(
            f'{{"index":{index},"judgement":"overlaps","confidence":0.5}}'
            for index in range(8)
        )
        + "]"
    )
    gateway, calls = _gateway_with_response(content)

    result = gateway.judge_batch("claim", ["passage"] * 8)

    assert result.error is None
    assert len(result.hits) == 8
    assert calls[0]["max_tokens"] == 2048
    assert "extra_body" not in calls[0]


def test_empty_judgement_response_is_reported_as_error() -> None:
    gateway, _ = _gateway_with_response("", finish_reason="length")

    result = gateway.judge_batch("claim", ["passage"])

    assert result.error is not None
    assert "empty content" in result.error
    assert result.hits[0].judgement == "unknown"
    assert result.hits[0].confidence == 0.0


def test_judgement_uses_configured_backup_provider() -> None:
    calls: list[tuple[str, dict]] = []

    def primary_create(**kwargs):
        calls.append(("primary", kwargs))
        raise RuntimeError("primary unavailable")

    def backup_create(**kwargs):
        calls.append(("backup", kwargs))
        return SimpleNamespace(
            model="backup-model",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='[{"index":0,"judgement":"supports","confidence":0.8}]'
                    ),
                    finish_reason="stop",
                )
            ]
        )

    gateway = JudgementGateway(
        api_key="remote-key",
        base_url="https://remote.example/v1",
        model="remote-model",
        backup_api_key="backup-key",
        backup_base_url="https://backup.example/v1",
        backup_model="backup-model",
    )
    gateway._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create))
    )
    gateway._backup_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=backup_create))
    )

    result = gateway.judge_batch("claim", ["passage"])

    assert result.error is None
    assert result.hits[0].judgement == "supports"
    assert result.model == "backup-model"
    assert [name for name, _ in calls] == ["primary", "backup"]
    assert calls[1][1]["model"] == "backup-model"
    assert "extra_body" not in calls[1][1]
