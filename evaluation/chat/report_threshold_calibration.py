"""报告诊断性的假阳性/假阴性，不批准任何阈值。

Chat QA 评估器刻意不设置生产环境的可回答性阈值。
这个只读辅助工具使用一个明确的诊断代理指标——将 ``mechanical_passed``
视为 “supported”——与已经人工标注的 ``human_verdict`` 字段进行比较。
它用于说明结构化引用检查为什么不能替代事实复核；不会编辑 Gold、观测记录或 Chat
消息，也不会调用 LLM。

示例：

    backend\\.venv\\Scripts\\python.exe \\
      evaluation\\chat\\report_threshold_calibration.py \\
      --report evaluation\\chat\\reports\\gnn_explanations_gold_v1_report.json \\
      --report evaluation\\chat\\reports\\gnn_explanations_gold_v2_report.json \\
      --output evaluation\\chat\\reports\\gnn_explanations_threshold_calibration.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PROXY_ID = "mechanical_passed_as_supported_proxy"


def _calibrate_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    false_positive_ids: list[str] = []
    false_negative_ids: list[str] = []
    unlabeled_ids: list[str] = []
    observed = 0

    for item in items:
        human_verdict = item.get("human_verdict")
        query_id = str(item.get("query_id") or "")
        if not query_id:
            raise ValueError("calibration report item is missing query_id")
        if human_verdict is None:
            unlabeled_ids.append(query_id)
            continue
        if human_verdict not in {"supported", "insufficient_evidence", "unsupported"}:
            raise ValueError(f"unsupported human_verdict for {query_id}: {human_verdict!r}")

        observed += 1
        predicted_supported = item.get("mechanical_passed") is True
        human_supported = human_verdict == "supported"
        if predicted_supported and not human_supported:
            false_positive_ids.append(query_id)
        elif not predicted_supported and human_supported:
            false_negative_ids.append(query_id)

    return {
        "labeled_observations": observed,
        "unlabeled_observations": len(unlabeled_ids),
        "unlabeled_query_ids": sorted(unlabeled_ids),
        "false_positive_count": len(false_positive_ids),
        "false_positive_query_ids": sorted(false_positive_ids),
        "false_negative_count": len(false_negative_ids),
        "false_negative_query_ids": sorted(false_negative_ids),
    }


def build_report(report_paths: list[Path]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    all_items: list[dict[str, Any]] = []
    for path in report_paths:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError(f"{path} does not contain a non-empty evaluator items list")
        case = _calibrate_items(items)
        case.update(
            {
                "case_id": payload.get("case_id") or payload.get("gold_case_id"),
                "source_report": path.name,
                "annotation_status": payload.get("annotation_status", "unknown"),
            }
        )
        cases.append(case)
        all_items.extend(items)

    aggregate = _calibrate_items(all_items)
    return {
        "schema_version": "1.0",
        "report": "chat_threshold_calibration_diagnostic",
        "candidate_rule": PROXY_ID,
        "candidate_type": "structural_proxy_not_numeric_threshold",
        "production_approved": False,
        "human_verdict_is_authoritative": True,
        "llm_called": False,
        "workspace_mutated": False,
        "gold_or_observations_modified": False,
        "decision": "do_not_promote_mechanical_pass_to_factual_answerability",
        "cases": cases,
        "aggregate": aggregate,
        "notes": [
            "False positives mean mechanical citation/source checks passed while human_verdict was not supported.",
            "False negatives mean mechanical checks failed while human_verdict was supported.",
            "A numeric retrieval or evidence threshold still requires human confirmation of semantics and error cost.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = build_report(args.report)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    aggregate = report["aggregate"]
    print(
        f"Calibrated {aggregate['labeled_observations']} labeled observations: "
        f"FP={aggregate['false_positive_count']}, "
        f"FN={aggregate['false_negative_count']} -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
