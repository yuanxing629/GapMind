"""W1 external full-text verification loop tests.

Covers the download/import failure paths (URL normalization, import_failed),
the metadata -> full_text evidence-level upgrade, full-text role re-judging
(LLM on paper text instead of title+abstract), and the failed-pipeline
degradation path.

Follows the W1 plan: URL normalization so non-https openAccessPdf URLs still
import, and role re-judgement against the imported paper's full text once the
pipeline is ready.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.domains.discover.models import (  # noqa: E402
    DiscoverExternalCandidate,
    DiscoverRun,
)
from app.domains.discover.external_retrieval import ExternalRetrievalService  # noqa: E402
from app.domains.discover.service import DiscoverService  # noqa: E402
from app.domains.knowledge.models import EvidenceSpan, KnowledgeItem  # noqa: E402
from app.domains.paper.models import Paper  # noqa: E402
from app.domains.task.models import Task  # noqa: E402
from app.domains.workspace.models import Workspace  # noqa: E402
from app.domains.artifact.models import Artifact  # noqa: E402
from app.gateway.semantic_scholar import SemanticScholarClient, SemanticScholarError  # noqa: E402


class _NoopLLM:
    def chat_completion(self, messages, **kwargs):
        return SimpleNamespace(content=json.dumps({"role": "unknown", "confidence": 0.3}))


class _RoleLLM:
    """Full-text role judge fake; records the user prompt for assertions."""

    def __init__(self, role: str = "contradicts", confidence: float = 0.8) -> None:
        self.role = role
        self.confidence = confidence
        self.messages: list[list[dict[str, str]]] = []

    def chat_completion(self, messages, **kwargs):
        self.messages.append(messages)
        return SimpleNamespace(content=json.dumps({"role": self.role, "confidence": self.confidence}))


class _BoomLLM:
    def chat_completion(self, messages, **kwargs):
        raise RuntimeError("llm down")


def _service(db, llm=None) -> DiscoverService:
    return DiscoverService(db, llm=llm or _NoopLLM())


def _ws(db) -> Workspace:
    ws = Workspace(id=str(uuid4()), name="ws")
    db.add(ws)
    db.commit()
    db.refresh(ws)
    return ws


def _run(db, ws: Workspace, **overrides) -> DiscoverRun:
    kwargs = {
        "id": str(uuid4()),
        "workspace_id": ws.id,
        "status": "running",
        "input_payload": {},
        "scope": {},
        "config": {},
        "stage_summaries": {},
    }
    kwargs.update(overrides)
    run = DiscoverRun(**kwargs)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _candidate(db, run: DiscoverRun, *, url: str | None = None, status: str = "selected", **overrides) -> DiscoverExternalCandidate:
    kwargs = {
        "id": str(uuid4()),
        "discover_run_id": run.id,
        "query": "topic",
        "rank": 1,
        "external_paper_id": "S2-1",
        "title": "External paper",
        "authors": [],
        "open_access_pdf": {"url": url} if url else None,
        "verification_status": status,
        "evidence_level": "metadata_only",
        "snapshot_payload": {},
    }
    kwargs.update(overrides)
    row = DiscoverExternalCandidate(**kwargs)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------- URL normalization


def test_normalize_pdf_url():
    svc = DiscoverService.__new__(DiscoverService)
    norm = svc._normalize_pdf_url
    assert norm("http://example.com/a.pdf") == "https://example.com/a.pdf"
    assert norm("//example.com/a.pdf") == "https://example.com/a.pdf"
    assert norm("https://arxiv.org/abs/1234.5678") == "https://arxiv.org/pdf/1234.5678"
    assert norm("https://arxiv.org/pdf/1234.5678") == "https://arxiv.org/pdf/1234.5678"
    assert norm("  https://example.com/a.pdf  ") == "https://example.com/a.pdf"
    assert norm("") == ""
    assert norm(None) == ""


# ------------------------------------------------------------------ import paths


def test_import_failed_when_download_raises(db_session):
    ws = _ws(db_session)
    run = _run(db_session, ws)
    row = _candidate(db_session, run, url="https://example.com/a.pdf")

    svc = _service(db_session)
    with patch.object(
        SemanticScholarClient,
        "download_pdf",
        side_effect=SemanticScholarError("boom", status_code=502),
    ):
        svc._import_selected_candidates(run)

    db_session.refresh(row)
    assert row.verification_status == "import_failed"
    assert "import_error" in (row.snapshot_payload or {})
    assert row.imported_paper_id is None


def test_import_normalizes_url_and_marks_pending_parse(db_session):
    ws = _ws(db_session)
    run = _run(db_session, ws)
    row = _candidate(db_session, run, url="http://example.com/a.pdf")

    downloaded: list[str] = []

    def fake_download(self, url, **kwargs):
        downloaded.append(url)
        return b"%PDF-1.4 fake"

    def fake_attach(self, *, workspace_id, paper_id, filename, content, mime_type=None):
        paper = db_session.get(Paper, paper_id)
        paper.primary_artifact_id = "artifact-fake"
        paper.parse_status = "pending"
        db_session.commit()
        return paper

    svc = _service(db_session)
    with patch.object(SemanticScholarClient, "download_pdf", new=fake_download):
        with patch("app.domains.paper.service.PaperService.attach_pdf_to_existing", new=fake_attach):
            svc._import_selected_candidates(run)

    db_session.refresh(row)
    # http:// URL is normalized to https:// before hitting download_pdf
    assert downloaded == ["https://example.com/a.pdf"]
    assert row.verification_status == "imported_pending_parse"
    assert row.imported_paper_id is not None
    assert row.evidence_level == "metadata_only"


def test_no_pdf_when_url_missing_and_no_arxiv_fallback(db_session):
    ws = _ws(db_session)
    run = _run(db_session, ws)
    row = _candidate(db_session, run, url=None, snapshot_payload={})

    svc = _service(db_session)
    with patch.object(SemanticScholarClient, "download_pdf") as fake:
        svc._import_selected_candidates(run)

    db_session.refresh(row)
    assert row.verification_status == "no_pdf"
    assert row.snapshot_payload["pdf_acquisition"]["status"] == "no_pdf"
    fake.assert_not_called()


def test_arxiv_fallback_uses_pdf_url(db_session):
    ws = _ws(db_session)
    run = _run(db_session, ws)
    row = _candidate(
        db_session, run, url=None,
        snapshot_payload={"externalIds": {"ArXiv": "arXiv:1234.5678"}},
    )

    downloaded: list[str] = []

    def fake_download(self, url, **kwargs):
        downloaded.append(url)
        return b"%PDF-1.4 fake"

    def fake_attach(self, *, workspace_id, paper_id, filename, content, mime_type=None):
        paper = db_session.get(Paper, paper_id)
        paper.primary_artifact_id = "artifact-fake"
        paper.parse_status = "pending"
        db_session.commit()
        return paper

    svc = _service(db_session)
    with patch.object(SemanticScholarClient, "download_pdf", new=fake_download):
        with patch("app.domains.paper.service.PaperService.attach_pdf_to_existing", new=fake_attach):
            svc._import_selected_candidates(run)

    db_session.refresh(row)
    assert downloaded == ["https://arxiv.org/pdf/1234.5678"]
    assert row.verification_status == "imported_pending_parse"


# ------------------------------------------------ metadata -> full_text upgrade


def _ready_pipeline(db_session, ws: Workspace, run: DiscoverRun, row: DiscoverExternalCandidate) -> str:
    """Attach a fully-ready imported paper to the candidate row."""
    paper_id = str(uuid4())
    artifact_id = str(uuid4())
    paper = Paper(
        id=paper_id, workspace_id=ws.id, title="Imported", authors=[],
        source="semantic_scholar", external_paper_id=row.external_paper_id,
        parse_status="parsed", parsed_markdown_artifact_id=artifact_id,
        parsed_text_artifact_id=str(uuid4()), extract_status="extracted", is_deleted=False,
    )
    artifact = Artifact(id=artifact_id, workspace_id=ws.id, kind="parsed_markdown", file_path="p.md", size_bytes=1, is_deleted=False)
    item = KnowledgeItem(id=str(uuid4()), workspace_id=ws.id, paper_id=paper_id, type="claim", canonical_name="claim", content={}, source_provenance={}, created_by="agent", is_deleted=False)
    db_session.add_all([paper, artifact, item])
    db_session.flush()
    db_session.add(EvidenceSpan(id=str(uuid4()), workspace_id=ws.id, knowledge_item_id=item.id, paper_id=paper_id, artifact_id=artifact_id, relation="supports", text="supporting", start_char=0, end_char=9, confidence=0.9))
    db_session.add(Task(id=str(uuid4()), workspace_id=ws.id, task_type="embed_chunks", status="succeeded", progress=1.0, payload={"paper_id": paper_id}, result={"indexed_count": 3}, is_deleted=False))
    row.imported_paper_id = paper_id
    row.verification_status = "imported_pending_parse"
    db_session.commit()
    return paper_id


def test_external_candidate_state_upgrades_to_full_text(db_session):
    ws = _ws(db_session)
    run = _run(db_session, ws)
    row = _candidate(db_session, run, status="imported_pending_parse", url=None)
    _ready_pipeline(db_session, ws, run, row)

    state = _service(db_session)._external_candidate_state(run)

    db_session.refresh(row)
    assert state["verified"] == 1
    assert row.verification_status == "verified"
    assert row.evidence_level == "full_text"


def test_external_candidate_state_failed_pipeline(db_session):
    ws = _ws(db_session)
    run = _run(db_session, ws)
    row = _candidate(db_session, run, status="imported_pending_parse", url=None)
    paper_id = str(uuid4())
    db_session.add(Paper(id=paper_id, workspace_id=ws.id, title="Imported", authors=[], source="semantic_scholar", external_paper_id=row.external_paper_id, parse_status="failed", parsed_markdown_artifact_id=str(uuid4()), extract_status="not_applicable", is_deleted=False))
    row.imported_paper_id = paper_id
    db_session.commit()

    state = _service(db_session)._external_candidate_state(run)

    db_session.refresh(row)
    assert state["failed"] == 1
    assert row.verification_status == "verification_failed"
    assert "verification_error" in (row.snapshot_payload or {})


# ---------------------------------------------------------- full-text role re-judge


def test_fulltext_role_judge_updates_role_and_is_idempotent(db_session):
    ws = _ws(db_session)
    run = _run(db_session, ws)
    row = _candidate(db_session, run, status="imported_pending_parse", url=None)
    _ready_pipeline(db_session, ws, run, row)
    _service(db_session)._external_candidate_state(run)  # pipeline ready -> verified
    db_session.refresh(row)
    assert row.verification_status == "verified"
    assert row.evidence_level == "full_text"

    llm = _RoleLLM(role="contradicts", confidence=0.9)
    svc = _service(db_session, llm=llm)
    with patch.object(ExternalRetrievalService, "_read_paper_text", return_value="full text showing evidence against the research question"):
        judged = svc._judge_external_fulltext_roles(run, "Is graph rationalization stable under shift?")

    assert judged == 1
    db_session.refresh(row)
    assert row.role == "contradicts"
    assert row.role_confidence == 0.9
    assert (row.snapshot_payload or {}).get("fulltext_role_judged") is True
    # Full text reached the LLM, not just the abstract.
    assert "full text showing evidence" in llm.messages[0][-1]["content"]

    # Idempotent: already-judged rows are not re-judged (no second LLM call).
    with patch.object(ExternalRetrievalService, "_read_paper_text", return_value="x"):
        judged2 = svc._judge_external_fulltext_roles(run, "Is graph rationalization stable under shift?")
    assert judged2 == 0
    assert len(llm.messages) == 1


def test_fulltext_role_judge_degrades_on_llm_failure(db_session):
    ws = _ws(db_session)
    run = _run(db_session, ws)
    row = _candidate(db_session, run, status="imported_pending_parse", url=None)
    _ready_pipeline(db_session, ws, run, row)
    _service(db_session)._external_candidate_state(run)  # pipeline ready -> verified

    svc = _service(db_session, llm=_BoomLLM())
    with patch.object(ExternalRetrievalService, "_read_paper_text", return_value="full text"):
        judged = svc._judge_external_fulltext_roles(run, "question")

    assert judged == 0
    db_session.refresh(row)
    assert row.role == "unknown"  # metadata role kept
    assert (row.snapshot_payload or {}).get("fulltext_role_tried") is True


def test_fulltext_role_judge_skips_without_imported_paper(db_session):
    ws = _ws(db_session)
    run = _run(db_session, ws)
    row = _candidate(db_session, run, status="verified", url=None)  # no imported_paper_id

    svc = _service(db_session)
    with patch.object(ExternalRetrievalService, "_read_paper_text") as fake:
        judged = svc._judge_external_fulltext_roles(run, "question")

    assert judged == 0
    fake.assert_not_called()
