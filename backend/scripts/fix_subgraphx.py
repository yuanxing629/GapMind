"""Fix SubgraphX: re-parse with the fixed pdf_parser, rebuild all artifacts.

SubgraphX's arXiv PDF typesets headings at ~11.95pt which the old
`is_large >= 12.0` threshold rejected → only 4 appendix chunks were indexed.
The pdf_parser threshold is now 11.5; this script re-runs the full pipeline
for SubgraphX so its body text is chunked and extracted properly.

Steps (mirrors workers/tasks/parse_pdf._run_parse_pdf + rebuild_paper_chunks):
  1. re-parse the PDF with the fixed parser
  2. save a NEW parsed_markdown artifact, update paper pointer
  3. re-chunk + create a new chunk_index Artifact + update chunk_count
  4. force-reindex Milvus (drop old vectors, insert new)
  5. soft-delete the OLD knowledge_items / evidence_spans (they anchor to the
     old 1-section markdown)
  6. trigger a fresh synchronous extraction (new Task + _run_extract)
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.db.models  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.domains.artifact.chunker import chunk_parsed_pdf  # noqa: E402
from app.domains.artifact.models import Artifact  # noqa: E402
from app.domains.artifact.pdf_parser import parse_pdf  # noqa: E402
from app.domains.artifact.service import ArtifactService  # noqa: E402
from app.domains.knowledge.models import EvidenceSpan, KnowledgeItem  # noqa: E402
from app.domains.paper.models import Paper  # noqa: E402
from app.domains.retrieval.service import index_paper_chunks  # noqa: E402
from app.domains.task.models import Task  # noqa: E402
from app.domains.task.schemas import TaskCreate  # noqa: E402
from app.domains.task.service import TaskService  # noqa: E402
from app.workers.tasks.extract_knowledge import _run_extract  # noqa: E402
from app.workers.tasks.parse_pdf import _chunk_to_dict  # noqa: E402

PAPER_ID = "ef64903b-96af-4cda-a751-e85786e1d813"  # SubgraphX


def main() -> int:
    db = SessionLocal()
    try:
        paper = db.get(Paper, PAPER_ID)
        if paper is None or paper.is_deleted:
            print(f"paper not found: {PAPER_ID}")
            return 1
        pdf_artifact = db.get(Artifact, paper.primary_artifact_id)
        if pdf_artifact is None:
            print("no PDF artifact")
            return 1

        # 1. Re-parse with the fixed parser.
        pdf_bytes = ArtifactService(db).resolve_abs_path(pdf_artifact).read_bytes()
        parsed = parse_pdf(pdf_bytes)
        print(f"parse: {len(parsed.full_text)} chars, sections={len(parsed.sections)}")

        # 2. Save NEW parsed_markdown artifact + update paper pointer.
        artifacts = ArtifactService(db)
        parsed_md = parsed.to_markdown()
        md_artifact = artifacts.save_upload(
            workspace_id=paper.workspace_id,
            filename=f"{paper.id}_reparsed.md",
            content=parsed_md.encode("utf-8"),
            mime_type="text/markdown",
            kind="parsed_markdown",
        )
        paper.parsed_markdown_artifact_id = md_artifact.id

        # 3. Re-chunk + create the canonical chunk_index Artifact.
        chunks = chunk_parsed_pdf(
            parsed,
            workspace_id=paper.workspace_id,
            paper_id=paper.id,
            created_at=datetime.now(UTC).isoformat(),
            source_artifact_id=paper.parsed_text_artifact_id,
        )
        chunk_payload = "\n".join(
            json.dumps(_chunk_to_dict(chunk), ensure_ascii=False)
            for chunk in chunks
        )
        chunk_artifact = artifacts.save_upload(
            workspace_id=paper.workspace_id,
            filename=f"{paper.id}_chunks_rebuilt.jsonl",
            content=chunk_payload.encode("utf-8"),
            mime_type="application/jsonl",
            kind="chunk_index",
        )
        paper.chunk_index_artifact_id = chunk_artifact.id
        paper.chunk_count = len(chunks)
        db.commit()
        print(f"chunks: {len(chunks)}")

        # 4. Force-reindex Milvus.
        result = index_paper_chunks(
            paper.workspace_id,
            paper.id,
            db=db,
            force_reindex=True,
        )
        print(f"index: total={result.total_chunks} indexed={result.indexed_count} skipped={result.skipped_count}")

        # 5. Soft-delete OLD knowledge items / evidence (anchored to old md).
        old_items = db.query(KnowledgeItem).filter(
            KnowledgeItem.paper_id == paper.id, KnowledgeItem.is_deleted.is_(False)
        ).all()
        for ki in old_items:
            ki.is_deleted = True
        old_spans = db.query(EvidenceSpan).filter(
            EvidenceSpan.paper_id == paper.id
        ).all()
        for es in old_spans:
            es.is_deleted = True
        db.commit()
        print(f"soft-deleted old items: {len(old_items)}, spans: {len(old_spans)}")

        # 6. Fresh synchronous extraction (new Task + _run_extract).
        task = TaskService(db).create(
            TaskCreate(
                workspace_id=paper.workspace_id,
                task_type="extract_knowledge",
                payload={"paper_id": paper.id},
            )
        )
        result = _run_extract(db, task.id)
        print(f"extract: status={result.get('status')}")
        return 0 if result.get("status") == "succeeded" else 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
