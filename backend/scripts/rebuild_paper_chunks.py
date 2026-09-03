"""Rebuild exact-source chunks without rerunning knowledge extraction.

This repair utility is intended for papers parsed before the exact chunk
slice fix. It creates a new immutable chunk_index artifact, updates the
Paper pointer, and force-reindexes Milvus.

Run from backend/:

    python scripts/rebuild_paper_chunks.py --paper-id <uuid> --paper-id <uuid>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.db.models  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.domains.artifact.chunker import chunk_parsed_pdf  # noqa: E402
from app.domains.artifact.models import Artifact  # noqa: E402
from app.domains.artifact.pdf_parser import parse_pdf  # noqa: E402
from app.domains.artifact.service import ArtifactService  # noqa: E402
from app.domains.paper.models import Paper  # noqa: E402
from app.domains.retrieval.service import index_paper_chunks  # noqa: E402
from app.workers.tasks.parse_pdf import _chunk_to_dict  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--paper-id",
        action="append",
        required=True,
        dest="paper_ids",
    )
    parser.add_argument(
        "--skip-milvus",
        action="store_true",
        help="Rebuild JSONL and Paper pointer without force-reindexing Milvus.",
    )
    return parser.parse_args()


def rebuild_paper(paper_id: str, *, skip_milvus: bool) -> None:
    db = SessionLocal()
    try:
        paper = db.get(Paper, paper_id)
        if paper is None or paper.is_deleted:
            raise RuntimeError(f"Paper not found: {paper_id}")
        if not paper.primary_artifact_id or not paper.parsed_text_artifact_id:
            raise RuntimeError(f"Paper lacks PDF or parsed_text: {paper_id}")

        artifacts = ArtifactService(db)
        pdf_artifact = db.get(Artifact, paper.primary_artifact_id)
        text_artifact = db.get(Artifact, paper.parsed_text_artifact_id)
        if pdf_artifact is None or text_artifact is None:
            raise RuntimeError(f"Paper artifacts are missing: {paper_id}")

        parsed = parse_pdf(artifacts.resolve_abs_path(pdf_artifact).read_bytes())
        existing_text = artifacts.resolve_abs_path(text_artifact).read_text(
            encoding="utf-8"
        )
        if parsed.full_text != existing_text:
            raise RuntimeError(
                "Reparsed text differs from the current parsed_text artifact; "
                "use a full reparse instead."
            )

        chunks = chunk_parsed_pdf(
            parsed,
            workspace_id=paper.workspace_id,
            paper_id=paper.id,
            created_at=datetime.now(UTC).isoformat(),
            source_artifact_id=text_artifact.id,
        )
        invalid = [
            chunk.chunk_index
            for chunk in chunks
            if chunk.text
            != existing_text[chunk.start_char : chunk.end_char]
        ]
        if invalid:
            raise RuntimeError(f"Exact chunk validation failed: {invalid[:10]}")

        payload = "\n".join(
            json.dumps(_chunk_to_dict(chunk), ensure_ascii=False)
            for chunk in chunks
        )
        chunk_artifact = artifacts.save_upload(
            workspace_id=paper.workspace_id,
            filename=f"{paper.id}_chunks_rebuilt.jsonl",
            content=payload.encode("utf-8"),
            mime_type="application/jsonl",
            kind="chunk_index",
        )
        paper = db.get(Paper, paper.id)
        paper.chunk_index_artifact_id = chunk_artifact.id
        paper.chunk_count = len(chunks)
        db.commit()

        print(
            f"{paper.id}: rebuilt {len(chunks)} chunks, "
            f"artifact={chunk_artifact.id}"
        )
        if not skip_milvus:
            result = index_paper_chunks(
                paper.workspace_id,
                paper.id,
                db=db,
                force_reindex=True,
            )
            if result.error:
                raise RuntimeError(result.error)
            print(
                f"{paper.id}: Milvus indexed={result.indexed_count}, "
                f"skipped={result.skipped_count}"
            )
    finally:
        db.close()


def main() -> int:
    args = parse_args()
    for paper_id in args.paper_ids:
        rebuild_paper(paper_id, skip_milvus=args.skip_milvus)
    return 0


if __name__ == "__main__":
    sys.exit(main())
