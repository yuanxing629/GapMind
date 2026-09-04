"""Retrieval Gate gold-set schema 和 paper-ref 解析测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evaluation.retrieval.gold_set import GoldSet  # noqa: E402
from evaluation.retrieval.run_eval import resolve_paper_ref  # noqa: E402

from app.domains.paper.models import Paper  # noqa: E402
from app.domains.workspace.models import Workspace  # noqa: E402
from app.db.base import Base  # noqa: E402


@pytest.fixture
def db_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session_ = sessionmaker(bind=engine)
    session = Session_()
    yield session
    session.close()


def _workspace(db: Session) -> Workspace:
    ws = Workspace(id="ws-1", name="Test", is_deleted=False)
    db.add(ws)
    db.commit()
    return ws


def _paper(db: Session, ws_id: str, title: str, external_id: str | None = None) -> Paper:
    paper = Paper(
        id=f"paper-{abs(hash(title)) % 100000}",
        workspace_id=ws_id,
        title=title,
        authors=[],
        source="manual",
        external_paper_id=external_id,
        is_deleted=False,
    )
    db.add(paper)
    db.commit()
    return paper


# ------------------------------------------------------------ gold schema：黄金集结构
def test_gold_set_requires_at_least_one_benchmark() -> None:
    with pytest.raises(ValidationError):
        GoldSet(
            case_id="c",
            corpus_version="v",
            semantic_search=[],
            similar_work=[],
            counter_evidence=[],
        )


def test_gold_set_accepts_single_benchmark() -> None:
    gold = GoldSet(
        case_id="c",
        corpus_version="v",
        semantic_search=[
            {"query_id": "ss-1", "query": "some query text here", "target_paper_ref": "X"}
        ],
    )
    assert len(gold.semantic_search) == 1
    assert not gold.similar_work


def test_gold_set_rejects_invalid_counter_role() -> None:
    with pytest.raises(ValidationError):
        GoldSet(
            case_id="c",
            corpus_version="v",
            counter_evidence=[
                {
                    "query_id": "ce-1",
                    "claim_text": "a claim about something",
                    "source_paper_ref": "S",
                    "gold_roles": [{"paper_ref": "T", "role": "not_a_real_role"}],
                }
            ],
        )


def test_gold_set_roundtrips_freezer() -> None:
    gold = GoldSet.model_validate(
        {
            "case_id": "c",
            "corpus_version": "v",
            "freeze": {"chunk_version": "v2", "embedding_model": "custom-emb"},
            "similar_work": [
                {"query_id": "sw-1", "source_paper_ref": "S", "relevant_paper_refs": ["A", "B"]}
            ],
        }
    )
    assert gold.freeze.chunk_version == "v2"
    assert gold.freeze.embedding_model == "custom-emb"


# ------------------------------------------------------------ paper refs：论文引用
def test_resolve_by_uuid(db_session: Session) -> None:
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id, "Title A")
    resolved = resolve_paper_ref(db_session, ws.id, paper.id)
    assert resolved is not None
    assert resolved.id == paper.id


def test_resolve_by_external_id(db_session: Session) -> None:
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id, "Title B", external_id="S2:abc123")
    resolved = resolve_paper_ref(db_session, ws.id, "S2:abc123")
    assert resolved is not None
    assert resolved.id == paper.id


def test_resolve_by_exact_title(db_session: Session) -> None:
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id, "Prototype-based Graph Information Bottleneck")
    resolved = resolve_paper_ref(db_session, ws.id, paper.title)
    assert resolved is not None
    assert resolved.id == paper.id


def test_resolve_by_title_case_insensitive(db_session: Session) -> None:
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id, "GNNExplainer: Generating Explanations")
    resolved = resolve_paper_ref(db_session, ws.id, "gnnexplainer: generating explanations")
    assert resolved is not None
    assert resolved.id == paper.id


def test_resolve_does_not_leak_across_workspaces(db_session: Session) -> None:
    ws_a = _workspace(db_session)
    ws_b = Workspace(id="ws-2", name="Other", is_deleted=False)
    db_session.add(ws_b)
    db_session.commit()
    paper = _paper(db_session, ws_a.id, "Private Paper")
# 在其他 workspace 查询同名标题时绝不能解析成功。
    resolved = resolve_paper_ref(db_session, ws_b.id, "Private Paper")
    assert resolved is None


def test_resolve_missing_returns_none(db_session: Session) -> None:
    ws = _workspace(db_session)
    assert resolve_paper_ref(db_session, ws.id, "No Such Paper Anywhere") is None


def test_resolve_ignores_soft_deleted(db_session: Session) -> None:
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id, "Deleted Paper")
    paper.is_deleted = True
    db_session.commit()
    resolved = resolve_paper_ref(db_session, ws.id, "Deleted Paper")
    assert resolved is None
