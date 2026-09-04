"""聚合持久化的 Chat 生成观测，不导出内容。

该只读报告用于 RAG generation-quality gate。它读取一个 workspace 中已完成和未完成的
assistant 行，只输出数量和数值聚合，绝不会将消息文本、来源文本、消息 ID 或 provider
错误写入报告。

示例：

    backend\\.venv\\Scripts\\python.exe \\
      evaluation\\chat\\report_generation_observability.py \\
      --workspace-id <workspace-id> \\
      --output evaluation\\chat\\reports\\generation_observability.json
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from math import floor
from pathlib import Path
import sys
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
for import_root in (REPO_ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from sqlalchemy import select  # noqa: E402

import app.db.models  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.domains.chat.models import ChatConversation, ChatMessage  # noqa: E402


NUMERIC_FIELDS = (
    "prompt_chars",
    "response_chars",
    "first_token_latency_ms",
    "completion_latency_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 2)


def _numeric_summary(values: Iterable[object | None]) -> dict[str, Any]:
    parsed = [float(value) for value in values if value is not None]
    return {
        "observed": len(parsed),
        "missing": None,
        "min": round(min(parsed), 2) if parsed else None,
        "p50": _percentile(parsed, 0.50),
        "p95": _percentile(parsed, 0.95),
        "max": round(max(parsed), 2) if parsed else None,
        "mean": round(sum(parsed) / len(parsed), 2) if parsed else None,
    }


def _summarize_rows(rows: list[ChatMessage]) -> dict[str, Any]:
    assistant_rows = [row for row in rows if row.role == "assistant"]
    completed = [row for row in assistant_rows if row.status == "completed"]
    result: dict[str, Any] = {
        "assistant_messages": len(assistant_rows),
        "completed_assistant_messages": len(completed),
        "status_counts": dict(sorted(Counter(row.status for row in assistant_rows).items())),
        "grounding_status_counts": dict(
            sorted(Counter(row.grounding_status for row in assistant_rows).items())
        ),
        "citation_quality_status_counts": dict(
            sorted(
                Counter(
                    (row.citation_quality or {}).get("status", "unknown")
                    for row in assistant_rows
                ).items()
            )
        ),
        "retrieval_audit_status_counts": dict(
            sorted(
                Counter(
                    (row.retrieval_audit or {}).get("status", "unknown")
                    for row in assistant_rows
                ).items()
            )
        ),
        "metrics": {},
    }
    for field in NUMERIC_FIELDS:
        summary = _numeric_summary(getattr(row, field) for row in completed)
        summary["missing"] = len(completed) - summary["observed"]
        result["metrics"][field] = summary
    return result


def build_report(workspace_id: str) -> dict[str, Any]:
    """读取活动 conversation 的 Chat 行并返回聚合报告。"""

    db = SessionLocal()
    try:
        rows = db.scalars(
            select(ChatMessage)
            .join(ChatConversation, ChatConversation.id == ChatMessage.conversation_id)
            .where(
                ChatConversation.workspace_id == workspace_id,
                ChatConversation.is_deleted.is_(False),
            )
            .order_by(ChatMessage.created_at, ChatMessage.sequence)
        ).all()
        return {
            "schema_version": "1.0",
            "report": "chat_generation_observability",
            "workspace_id": workspace_id,
            "read_only": True,
            "llm_called": False,
            "workspace_mutated": False,
            "raw_content_exported": False,
            "message_ids_exported": False,
            "scope": {
                "conversation_filter": "workspace_id and is_deleted=false",
                "assistant_rows_only_in_metrics": True,
                "latency_unit": "ms",
                "character_count": "Python len over persisted prompt context or final response",
            },
            "summary": _summarize_rows(rows),
        }
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build_report(args.workspace_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = report["summary"]
    print(
        f"Reported {summary['assistant_messages']} assistant messages "
        f"({summary['completed_assistant_messages']} completed) to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
