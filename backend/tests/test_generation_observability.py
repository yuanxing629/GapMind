"""保护隐私的生成观测聚合测试。"""

from __future__ import annotations

from types import SimpleNamespace

from evaluation.chat.report_generation_observability import (
    _numeric_summary,
    _summarize_rows,
)


def test_numeric_summary_reports_percentiles_and_missing_values() -> None:
    summary = _numeric_summary([1, 2, 3, None, 5])

    assert summary["observed"] == 4
    assert summary["p50"] == 2.5
    assert summary["p95"] == 4.7
    assert summary["missing"] is None


def test_generation_report_summary_contains_states_not_message_content() -> None:
    rows = [
        SimpleNamespace(
            role="assistant",
            status="completed",
            grounding_status="grounded",
            citation_quality={"status": "passed"},
            retrieval_audit={"status": "succeeded"},
            prompt_chars=10,
            response_chars=20,
            first_token_latency_ms=3.0,
            completion_latency_ms=8.0,
            prompt_tokens=2,
            completion_tokens=3,
            total_tokens=5,
        ),
        SimpleNamespace(
            role="assistant",
            status="failed",
            grounding_status="retrieval_failed",
            citation_quality={},
            retrieval_audit={},
            prompt_chars=None,
            response_chars=None,
            first_token_latency_ms=None,
            completion_latency_ms=None,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        ),
    ]

    summary = _summarize_rows(rows)

    assert summary["assistant_messages"] == 2
    assert summary["completed_assistant_messages"] == 1
    assert summary["status_counts"] == {"completed": 1, "failed": 1}
    assert summary["metrics"]["completion_latency_ms"]["p50"] == 8.0
    assert "content" not in summary
    assert "message_id" not in summary
