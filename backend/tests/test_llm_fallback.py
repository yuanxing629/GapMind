"""LLM 网关主备回退测试（演示日保险丝）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.gateway.llm import LLMGateway


def _resp(content: str, model: str = "m") -> SimpleNamespace:
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        model=model,
        usage=usage,
    )


def _stream_chunks(*deltas: str) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=delta))])
        for delta in deltas
    ]


class FakeCompletions:
    """create() 按预设结果列表执行：值表示成功，异常表示抛出。"""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _gateway(
    primary_outcomes: list[Any], backup_outcomes: list[Any] | None = None
) -> tuple[LLMGateway, FakeCompletions, FakeCompletions | None]:
    gateway = LLMGateway(
        api_key="primary-key",
        base_url="https://primary",
        model="primary-model",
        backup_api_key="backup-key",
        backup_base_url="https://backup",
        backup_model="backup-model",
    )
    primary = FakeCompletions(primary_outcomes)
    gateway._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=primary.create))
    )
    backup = None
    if backup_outcomes is not None:
        backup = FakeCompletions(backup_outcomes)
        gateway._backup_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=backup.create))
        )
    return gateway, primary, backup


def test_primary_success_never_touches_backup():
    gateway, primary, backup = _gateway(
        [_resp("ok", "primary-model")], [_resp("should-not-be-used", "backup-model")]
    )
    response = gateway.chat_completion([{"role": "user", "content": "hi"}])
    assert response.content == "ok"
    assert response.model == "primary-model"
    assert len(primary.calls) == 1
    assert backup is not None and backup.calls == []


def test_primary_failure_falls_over_to_backup():
    gateway, _, backup = _gateway(
        [RuntimeError("primary down")], [_resp("backup ok", "backup-model")]
    )
    response = gateway.chat_completion(
        [{"role": "user", "content": "hi"}], disable_thinking=True
    )
    assert response.content == "backup ok"
    assert backup is not None
    assert backup.calls[0]["model"] == "backup-model"
    # 备用端点接收相同的标准 Chat Completions 负载。
    assert "extra_body" not in backup.calls[0]


def test_generic_chat_completion_does_not_send_provider_specific_thinking_fields():
    # 标准 OpenAI 兼容端点只应接收标准字段。
    gateway, _, backup = _gateway(
        [RuntimeError("primary down")],
        [
            _resp("backup ok", "backup-model"),
        ],
    )
    response = gateway.chat_completion(
        [{"role": "user", "content": "hi"}], disable_thinking=True
    )
    assert response.content == "backup ok"
    assert backup is not None
    assert "extra_body" not in backup.calls[0]


def test_failure_without_backup_configured_raises_primary_error():
    gateway = LLMGateway(api_key="k", base_url="u", model="m")  # no backup fields
    assert gateway.backup_enabled is False
    primary = FakeCompletions([RuntimeError("primary down")])
    gateway._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=primary.create))
    )
    try:
        gateway.chat_completion([{"role": "user", "content": "hi"}])
        raise AssertionError("should have raised")
    except RuntimeError as exc:
        assert "primary down" in str(exc)


def test_backup_also_failing_raises_primary_error():
    gateway, _, _ = _gateway(
        [RuntimeError("primary down")],
        [RuntimeError("backup down too"), RuntimeError("backup down too")],
    )
    try:
        gateway.chat_completion(
            [{"role": "user", "content": "hi"}], disable_thinking=True
        )
        raise AssertionError("should have raised")
    except RuntimeError as exc:
        # 主端点错误更值得调查，因此优先抛出它。
        assert "primary down" in str(exc)


def test_stream_falls_over_before_first_chunk():
    gateway, primary, backup = _gateway(
        [RuntimeError("primary down")], [_stream_chunks("a", "b", "c")]
    )
    deltas = list(
        gateway.stream_chat_completion([{"role": "user", "content": "hi"}])
    )
    assert deltas == ["a", "b", "c"]
    assert len(primary.calls) == 1
    assert backup is not None and len(backup.calls) == 1
    assert backup.calls[0]["stream"] is True


def test_backup_requires_all_three_fields():
    partial = LLMGateway(
        api_key="k", base_url="u", model="m",
        backup_api_key="bk", backup_base_url="bu",  # missing backup_model
    )
    assert partial.backup_enabled is False


def test_vision_requests_use_the_dedicated_client_without_text_backup():
    gateway = LLMGateway(
        api_key="remote-key",
        base_url="https://remote.example/v1",
        model="text-model",
        vision_api_key="vision-key",
        vision_base_url="https://vision.example/v1",
        vision_model="vision-model",
        backup_api_key="backup-key",
        backup_base_url="https://backup.example/v1",
        backup_model="backup-model",
    )
    vision = FakeCompletions([_resp("vision ok", "vision-model")])
    gateway._vision_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=vision.create))
    )

    response = gateway.chat_completion(
        [{"role": "user", "content": [{"type": "text", "text": "describe"}]}],
        model_override="vision-model",
        disable_thinking=True,
    )

    assert response.content == "vision ok"
    assert vision.calls[0]["model"] == "vision-model"
    assert "extra_body" not in vision.calls[0]
