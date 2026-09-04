"""Retrieval Gate 指标函数的单元测试（纯函数，无 I/O）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 仓库根目录，使 `evaluation.retrieval.metrics` 可从 backend/tests/ 导入
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evaluation.retrieval.metrics import (  # noqa: E402
    gate_report,
    mrr_at_k,
    ndcg_at_k,
    paper_diversity,
    recall_at_k,
    workspace_leakage,
)


# --------------------------------------------------------------- recall@k：召回率
def test_recall_at_k_all_hit() -> None:
    assert recall_at_k({"a", "b"}, ["a", "b", "c", "d"], k=10) == 1.0


def test_recall_at_k_partial() -> None:
    assert recall_at_k({"a", "b", "c"}, ["a", "x", "y"], k=3) == pytest.approx(1 / 3)


def test_recall_at_k_respects_k() -> None:
# 两个 gold 条目都存在，但第二个超出 k=1。
    assert recall_at_k({"a", "b"}, ["a", "b"], k=1) == 0.5


def test_recall_at_k_empty_gold() -> None:
    assert recall_at_k(set(), ["a"], k=10) == 0.0


def test_recall_at_k_no_hits() -> None:
    assert recall_at_k({"z"}, ["a", "b"], k=10) == 0.0


# ------------------------------------------------------------------ mrr@k：平均倒数排名
def test_mrr_first_rank_is_one() -> None:
    assert mrr_at_k({"b"}, ["b", "a"], k=10) == 1.0


def test_mrr_second_rank_is_half() -> None:
    assert mrr_at_k({"b"}, ["a", "b", "c"], k=10) == pytest.approx(0.5)


def test_mrr_zero_when_absent() -> None:
    assert mrr_at_k({"z"}, ["a", "b"], k=10) == 0.0


def test_mrr_beyond_k_counts_zero() -> None:
# Gold 位于第 3 位，但只查看前 2 位。
    assert mrr_at_k({"c"}, ["a", "b", "c"], k=2) == 0.0


# ------------------------------------------------------------- 多样性
def test_diversity_full_when_all_distinct() -> None:
    assert paper_diversity(["a", "b", "c", "d"], k=10) == 1.0


def test_diversity_single_paper_dominates() -> None:
    assert paper_diversity(["a", "a", "a", "a"], k=10) == pytest.approx(0.25)


def test_diversity_limited_by_k() -> None:
# 返回的前 2 个槽位中有两个不同对象。
    assert paper_diversity(["a", "b", "a", "a", "a"], k=2) == 1.0


def test_diversity_empty() -> None:
    assert paper_diversity([], k=10) == 0.0


# -------------------------------------------------------------- 泄漏
def test_leakage_zero_when_all_match() -> None:
    assert workspace_leakage(["ws1", "ws1"], "ws1") == 0.0


def test_leakage_detects_foreign_workspace() -> None:
    assert workspace_leakage(["ws1", "ws2"], "ws1") == pytest.approx(0.5)


def test_leakage_empty_list() -> None:
    assert workspace_leakage([], "ws1") == 0.0


# ------------------------------------------------------------- gate_report：门禁报告
def test_gate_report_passes_when_recall_above_threshold() -> None:
    report = gate_report(recall=0.82, threshold=0.80, leakage=0.0)
    assert report["recall_passed"] is True
    assert report["passed"] is True


def test_gate_report_fails_on_leakage() -> None:
    report = gate_report(recall=0.95, threshold=0.80, leakage=0.1)
    assert report["passed"] is False  # recall fine, but leakage != 0


def test_gate_report_fails_below_threshold() -> None:
    report = gate_report(recall=0.65, threshold=0.70, leakage=0.0)
    assert report["recall_passed"] is False
    assert report["passed"] is False


def test_gate_report_uses_requested_k_in_metric_keys() -> None:
    report = gate_report(recall=0.82, threshold=0.80, k=15, mrr=0.5, ndcg=0.4)
    assert report["recall@15"] == 0.82
    assert report["mrr@15"] == 0.5
    assert report["ndcg@15"] == 0.4
    assert "recall@10" not in report
