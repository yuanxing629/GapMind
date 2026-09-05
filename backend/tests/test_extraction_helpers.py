"""从 Celery worker 拆出的抽取辅助函数单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.workers.tasks.extraction.batching import (
    DEFAULT_MAX_CHARS,
    split_extraction_batches,
)
from app.workers.tasks.extraction.llm_caller import (
    call_llm_with_retry,
    parse_llm_json,
)


# ---------------------------------------------------------------- batching：批处理
def test_split_short_text_returns_single_batch() -> None:
    assert split_extraction_batches("short") == [(0, "short")]


def test_split_long_text_preserves_heading_boundaries() -> None:
    body = "intro paragraph\n\n" + ("x" * (DEFAULT_MAX_CHARS - 50)) + "\n## Methods\n" + ("y" * 1000)
    batches = split_extraction_batches(body)
# 至少两个批次，且标题落在独立的批次边界上。
    assert len(batches) >= 2
    starts = [start for start, _ in batches]
    assert starts == sorted(starts)
# 最后一个批次恰好结束于文档长度（不丢弃尾部）。
    last_start, last_text = batches[-1]
    assert last_start + len(last_text) == len(body)


def test_split_never_drops_tail() -> None:
    body = "a" * (DEFAULT_MAX_CHARS * 2 + 250)
    batches = split_extraction_batches(body)
    joined = "".join(text for _, text in batches)
# 即使存在 overlap，并集也必须覆盖整个文档。
    assert joined.startswith(body[:100])
    assert joined.rstrip().endswith(body[-100:].rstrip())


# ---------------------------------------------------------------- LLM JSON：LLM JSON 解析
@pytest.mark.parametrize(
    "raw,expected",
    [
        ('```json\n{"items": []}\n```', {"items": []}),
        ("noise {\"items\": [1]} trailing", {"items": [1]}),
        ('{"items": [1],}', {"items": [1]}),  # trailing comma stripped
        ("not json", None),
        ('{"items": "string-not-list"}', {"items": "string-not-list"}),
    ],
)
def test_parse_llm_json_handles_common_shapes(raw: str, expected) -> None:
    assert parse_llm_json(raw) == expected


def test_call_llm_with_retry_returns_parsed_on_success() -> None:
    """第一次尝试成功 -> parsed dict + raw。"""
    fake_response = MagicMock(content='```json\n{"items": []}\n```')
    fake_gateway = MagicMock(chat_completion=MagicMock(return_value=fake_response))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.workers.tasks.extraction.llm_caller.LLMGateway", lambda: fake_gateway)
        raw, parsed = call_llm_with_retry(
            [{"role": "user", "content": "extract"}],
            max_retries=0,
        )

    assert parsed == {"items": []}
    assert raw == fake_response.content
    assert fake_gateway.chat_completion.call_count == 1
    call_kwargs = fake_gateway.chat_completion.call_args.kwargs
    assert call_kwargs["response_format"] == {"type": "json_object"}
    assert call_kwargs["disable_thinking"] is True


def test_call_llm_with_retry_recovers_on_second_attempt() -> None:
    """第一次响应格式错误、第二次正确 -> 返回正确响应。"""
    fake_response_good = MagicMock(content='{"items": ["a"]}')
    fake_gateway = MagicMock(chat_completion=MagicMock(side_effect=[
        MagicMock(content="not json at all"),
        fake_response_good,
    ]))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.workers.tasks.extraction.llm_caller.LLMGateway", lambda: fake_gateway)
        mp.setattr("app.workers.tasks.extraction.llm_caller.RETRY_BACKOFF_SECONDS", 0)
        raw, parsed = call_llm_with_retry(
            [{"role": "user", "content": "extract"}],
            max_retries=2,
        )

    assert parsed == {"items": ["a"]}
    assert fake_gateway.chat_completion.call_count == 2
    retry_messages = fake_gateway.chat_completion.call_args_list[1].args[0]
    assert retry_messages[-1]["role"] == "user"
    assert "compact and complete JSON" in retry_messages[-1]["content"]
    assert "method.content.inputs" in retry_messages[-1]["content"]
    assert "one-element" in retry_messages[-1]["content"]


def test_call_llm_with_retry_retries_empty_evidence_items() -> None:
    fake_gateway = MagicMock(
        chat_completion=MagicMock(
            side_effect=[
                MagicMock(
                    content='{"items": [{"type": "dataset", "evidence_text": ""}]}'
                ),
                MagicMock(
                    content='{"items": [{"type": "dataset", "evidence_text": "BBBP"}]}'
                ),
            ]
        )
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.workers.tasks.extraction.llm_caller.LLMGateway", lambda: fake_gateway)
        mp.setattr("app.workers.tasks.extraction.llm_caller.RETRY_BACKOFF_SECONDS", 0)
        raw, parsed = call_llm_with_retry(
            [{"role": "user", "content": "extract"}],
            max_retries=2,
        )

    assert parsed == {"items": [{"type": "dataset", "evidence_text": "BBBP"}]}
    assert raw.endswith('"BBBP"}]}')
    assert fake_gateway.chat_completion.call_count == 2


def test_call_llm_with_retry_returns_none_after_exhaustion() -> None:
    """每次尝试都失败 -> 保留 raw，parsed 为 None。"""
    fake_gateway = MagicMock(chat_completion=MagicMock(return_value=MagicMock(content="garbage")))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.workers.tasks.extraction.llm_caller.LLMGateway", lambda: fake_gateway)
        mp.setattr("app.workers.tasks.extraction.llm_caller.RETRY_BACKOFF_SECONDS", 0)
        raw, parsed = call_llm_with_retry(
            [{"role": "user", "content": "extract"}],
            max_retries=2,
        )

    assert parsed is None
    assert raw == "garbage"
    assert fake_gateway.chat_completion.call_count == 3  # 1 initial + 2 retries
