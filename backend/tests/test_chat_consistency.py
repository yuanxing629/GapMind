"""Evidence 一致性检查测试（W3-3）：[En] citation 校验。"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.domains.chat.consistency import (  # noqa: E402
    CITATION_PATTERN,
    check_citation_markers,
    message_citation_check,
    source_marker_check,
)


def test_check_citation_markers_valid() -> None:
    result = check_citation_markers("该结论基于 [E1] 与 [E3]。", {1, 2, 3})
    assert result.referenced == [1, 3]
    assert result.valid == [1, 3]
    assert result.broken == []
    assert result.ok is True


def test_check_citation_markers_broken() -> None:
    result = check_citation_markers("引用 [E1] 和 [E9]。", {1, 2, 3})
    assert result.referenced == [1, 9]
    assert result.valid == [1]
    assert result.broken == [9]
    assert result.ok is False


def test_check_citation_markers_no_markers() -> None:
    result = check_citation_markers("没有任何引用的结论。", {1, 2})
    assert result.referenced == []
    assert result.ok is True
    assert result.grounded_without_citations is False


def test_check_citation_markers_empty_text() -> None:
    result = check_citation_markers("", set())
    assert result.referenced == []
    assert result.ok is True


def test_message_citation_check_grounded_without_citations() -> None:
    result = message_citation_check("关键结论但没有任何 [E] 引用。", [1, 2], grounded=True)
    assert result.grounded_without_citations is True
    assert result.referenced == []


def test_message_citation_check_grounded_with_citations() -> None:
    result = message_citation_check("依据 [E1] 和 [E2]。", [1, 2], grounded=True)
    assert result.grounded_without_citations is False
    assert result.ok is True


def test_message_citation_check_not_grounded_ignores_unsupported() -> None:
    result = message_citation_check("无引用的回答。", [1], grounded=False)
    assert result.grounded_without_citations is False


def test_message_citation_check_mixed_valid_broken() -> None:
    result = message_citation_check("正确 [E1]，错误 [E7]。", [1, 2, 3], grounded=True)
    assert result.valid == [1]
    assert result.broken == [7]
    assert result.ok is False


def test_citation_pattern_matches_bracketed_indices() -> None:
    assert CITATION_PATTERN.findall("见 [E1] 和 [E12] 与 [E0]") == ["1", "12", "0"]


def test_source_markers_are_checked_against_non_paper_passport() -> None:
    result = source_marker_check("计划 [P1]，论文 [E1]，草案 [C1]。", {"[P1]", "[C1]"})
    assert result.referenced == ["[C1]", "[P1]"]
    assert result.broken == []
    assert result.ok is True


def test_source_markers_reject_unknown_plan_report_or_code_marker() -> None:
    result = source_marker_check("报告 [D2] 不应冒充来源。", {"[D1]"})
    assert result.broken == ["[D2]"]
    assert result.ok is False
