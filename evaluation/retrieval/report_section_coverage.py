"""报告 workspace 当前解析论文的章节覆盖情况。

这是针对实验性 section-facet 工作的只读语料审计。它跟踪每个 active Paper 当前的
``chunk_index_artifact_id``，并排除已软删除的 paper/artifact。它只报告数量，不导出 chunk
文本、标题、paper ID 或 artifact ID。因此 section hint 仅作为诊断证据，不用于硬检索过滤。
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
for import_root in (REPO_ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from sqlalchemy import select  # noqa: E402

import app.db.models  # noqa: E402,F401
from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.domains.artifact.models import Artifact  # noqa: E402
from app.domains.paper.models import Paper  # noqa: E402
from app.domains.retrieval.schemas import ChunkRecord  # noqa: E402


def _storage_root() -> Path:
    configured = Path(settings.app_storage_dir)
    return configured if configured.is_absolute() else BACKEND_ROOT / configured


def _read_chunk_sections(path: Path, workspace_id: str, paper_id: str) -> tuple[Counter[str], int, int]:
    counts: Counter[str] = Counter()
    invalid_lines = 0
    total_lines = 0
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            total_lines += 1
            try:
# PyMuPDF 生成的文本可能包含 NUL。它不会导出；在 JSON 校验前移除可保证审计安全。
                record = ChunkRecord.model_validate_json(line.replace("\x00", ""))
            except (ValueError, TypeError):
                invalid_lines += 1
                continue
            if record.workspace_id != workspace_id or record.paper_id != paper_id:
                invalid_lines += 1
                continue
            counts[record.section or "Unknown"] += 1
    return counts, total_lines, invalid_lines


def build_report(workspace_id: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(Paper, Artifact)
            .join(Artifact, Artifact.id == Paper.chunk_index_artifact_id)
            .where(
                Paper.workspace_id == workspace_id,
                Paper.is_deleted.is_(False),
                Artifact.workspace_id == workspace_id,
                Artifact.kind == "chunk_index",
                Artifact.is_deleted.is_(False),
            )
        ).all()
    finally:
        db.close()

    chunk_counts: Counter[str] = Counter()
    paper_counts: Counter[str] = Counter()
    missing_files = 0
    invalid_lines = 0
    total_lines = 0
    papers_with_current_index = 0
    root = _storage_root()
    for paper, artifact in rows:
        path = root / artifact.file_path
        if not path.is_file():
            missing_files += 1
            continue
        papers_with_current_index += 1
        sections, line_count, invalid_count = _read_chunk_sections(
            path, workspace_id, paper.id
        )
        total_lines += line_count
        invalid_lines += invalid_count
        chunk_counts.update(sections)
        for section in sections:
            paper_counts[section] += 1

    section_names = sorted(set(chunk_counts) | set(paper_counts))
    return {
        "schema_version": "1.0",
        "report": "retrieval_section_coverage",
        "workspace_id": workspace_id,
        "read_only": True,
        "llm_called": False,
        "workspace_mutated": False,
        "raw_content_exported": False,
        "ids_exported": False,
        "scope": {
            "paper_filter": "workspace_id and is_deleted=false",
            "artifact_filter": "current chunk_index_artifact_id, workspace_id, is_deleted=false",
            "section_filtering": "none; diagnostic coverage only",
        },
        "summary": {
            "papers_with_current_chunk_index": papers_with_current_index,
            "papers_missing_chunk_index_file": missing_files,
            "chunk_records_read": total_lines,
            "invalid_chunk_records": invalid_lines,
            "canonical_section_names": section_names,
            "chunk_counts_by_section": dict(sorted(chunk_counts.items())),
            "paper_counts_by_section": dict(sorted(paper_counts.items())),
            "unknown_chunk_count": chunk_counts.get("Unknown", 0),
            "unknown_paper_count": paper_counts.get("Unknown", 0),
        },
    }


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
        f"Audited {summary['papers_with_current_chunk_index']} papers and "
        f"{summary['chunk_records_read']} chunk records to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
