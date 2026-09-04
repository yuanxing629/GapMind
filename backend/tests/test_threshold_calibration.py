"""无阈值 Chat 校准诊断测试。"""

from __future__ import annotations

import pytest

from evaluation.chat.report_threshold_calibration import _calibrate_items


def test_calibration_reports_false_positive_and_false_negative() -> None:
    result = _calibrate_items(
        [
            {"query_id": "fp", "mechanical_passed": True, "human_verdict": "insufficient_evidence"},
            {"query_id": "fn", "mechanical_passed": False, "human_verdict": "supported"},
            {"query_id": "ok", "mechanical_passed": True, "human_verdict": "supported"},
            {"query_id": "pending", "mechanical_passed": True, "human_verdict": None},
        ]
    )

    assert result["false_positive_query_ids"] == ["fp"]
    assert result["false_negative_query_ids"] == ["fn"]
    assert result["unlabeled_query_ids"] == ["pending"]


def test_calibration_rejects_unknown_human_verdict() -> None:
    with pytest.raises(ValueError, match="unsupported human_verdict"):
        _calibrate_items(
            [{"query_id": "q1", "mechanical_passed": True, "human_verdict": "maybe"}]
        )
