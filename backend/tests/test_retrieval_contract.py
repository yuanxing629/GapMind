"""Retrieval 排除契约测试（RG-2 / D1）。

验证：
  1. ``milvus_client.search`` 将 ``exclude_paper_ids`` 下推到 filter 表达式（而不是事后
     过滤），因此被排除论文不会进入召回。
  2. 三个 retrieval service 函数转发排除集合，并在 ``filters_applied`` 中暴露以供审计。
  3. ``find_similar_work`` 始终排除自己的来源论文。
  4. workspace 隔离属于 Milvus filter 表达式的一部分。

所有 Milvus/embedding 交互都使用 mock；这些测试验证 contract wiring，而不是向量搜索本身。
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.domains.artifact.service import ArtifactService
from app.domains.paper.models import Paper
from app.domains.retrieval import milvus_client, service
from app.domains.retrieval.schemas import RetrievalResponse
from app.domains.workspace.models import Workspace


# ==================================================================
# milvus_client.search 的过滤器下推
# ==================================================================


class _RecordingMilvus:
    """记录过滤表达式的 pymilvus MilvusClient 替身。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def has_collection(self, name: str) -> bool:
        return True

    def load_collection(self, name: str) -> None:
        pass

    def search(self, *, collection_name, data, limit, filter, output_fields, search_params):
        self.calls.append({"filter": filter, "limit": limit, "data_len": len(data[0])})
        return [[]]


def _patch_milvus_client(monkeypatch) -> _RecordingMilvus:
    fake = _RecordingMilvus()
    monkeypatch.setattr(milvus_client, "get_milvus_client", lambda: fake)
    return fake


def test_search_excludes_papers_in_filter(monkeypatch) -> None:
    fake = _patch_milvus_client(monkeypatch)
    milvus_client.search([0.1] * 4, "ws-1", top_k=10, exclude_paper_ids={"p-2", "p-1"})
    expr = fake.calls[0]["filter"]
    assert 'workspace_id == "ws-1"' in expr
# 排序后得到确定性过滤字符串。
    assert 'paper_id not in ["p-1", "p-2"]' in expr


def test_search_combines_paper_id_and_exclude(monkeypatch) -> None:
    fake = _patch_milvus_client(monkeypatch)
    milvus_client.search(
        [0.1] * 4,
        "ws-1",
        top_k=10,
        paper_id="target",
        exclude_paper_ids={"p-9"},
        section="Method",
    )
    expr = fake.calls[0]["filter"]
    assert 'paper_id == "target"' in expr
    assert 'paper_id not in ["p-9"]' in expr
    assert 'section == "Method"' in expr


def test_search_workspace_isolation_in_filter(monkeypatch) -> None:
    fake = _patch_milvus_client(monkeypatch)
    milvus_client.search([0.1] * 4, "ws-42", top_k=5)
    expr = fake.calls[0]["filter"]
    assert 'workspace_id == "ws-42"' in expr
    assert expr.count("workspace_id") == 1  # not duplicated


def test_search_empty_exclude_omits_clause(monkeypatch) -> None:
    fake = _patch_milvus_client(monkeypatch)
    milvus_client.search([0.1] * 4, "ws-1", top_k=10, exclude_paper_ids=set())
    expr = fake.calls[0]["filter"]
    assert "paper_id not in" not in expr


# ==================================================================
# service 级排除条件转发
# ==================================================================


class _FakeEmbedding:
    def embed_one(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]

    def embed_texts(self, texts: list[str]):
        from types import SimpleNamespace

        return SimpleNamespace(embeddings=[[0.1, 0.2, 0.3, 0.4]] * len(texts))


class _FakeMilvus:
    """替换 ``service.milvus_client``，并记录搜索参数。"""

    def __init__(self, hits: list[dict[str, Any]]) -> None:
        self.hits = hits
        self.calls: list[dict[str, Any]] = []

    def search(self, query_vector, workspace_id, top_k=10, *, paper_id=None, exclude_paper_ids=None, section=None):
        self.calls.append(
            {
                "query_vector": query_vector,
                "workspace_id": workspace_id,
                "top_k": top_k,
                "paper_id": paper_id,
                "exclude_paper_ids": exclude_paper_ids,
                "section": section,
            }
        )
        return self.hits


def _patch_service_deps(monkeypatch, hits: list[dict[str, Any]]) -> _FakeMilvus:
    fake = _FakeMilvus(hits)
    monkeypatch.setattr(service, "milvus_client", fake)
    monkeypatch.setattr(service, "get_embedding_gateway", _FakeEmbedding)
    return fake


def test_counter_evidence_forwards_exclude_to_milvus(monkeypatch) -> None:
    fake = _patch_service_deps(
        monkeypatch,
        hits=[{"chunk_id": "c1", "workspace_id": "ws-1", "paper_id": "p-other",
               "section": "M", "text": "t", "score": 0.9,
               "source_artifact_id": "a1", "chunk_index": 1}],
    )
    resp = service.find_counter_evidence(
        "ws-1", "some claim", top_k=5,
        use_reranker=False, use_judge=False,
        exclude_paper_ids={"p-source"},
    )
    assert fake.calls[0]["exclude_paper_ids"] == {"p-source"}
    assert resp.filters_applied["excluded_paper_ids"] == ["p-source"]
    assert resp.items[0].paper_id == "p-other"  # only the NON-excluded paper survives


def test_counter_evidence_never_returns_excluded_paper(monkeypatch) -> None:
    """即使有缺陷的 Milvus 返回了被排除的分块，服务也必须在返回前防御性地丢弃它们。

    这是过滤下推之外的额外保护。"""
    hits = [
        {"chunk_id": "c1", "workspace_id": "ws-1", "paper_id": "p-source",
         "section": "M", "text": "t", "score": 0.9, "source_artifact_id": "a1", "chunk_index": 1},
        {"chunk_id": "c2", "workspace_id": "ws-1", "paper_id": "p-source",
         "section": "M", "text": "t2", "score": 0.8, "source_artifact_id": "a1", "chunk_index": 2},
        {"chunk_id": "c3", "workspace_id": "ws-1", "paper_id": "p-other",
         "section": "M", "text": "t3", "score": 0.7, "source_artifact_id": "a1", "chunk_index": 3},
    ]
    _patch_service_deps(monkeypatch, hits=hits)
    resp = service.find_counter_evidence(
        "ws-1", "some claim", top_k=5,
        use_reranker=False, use_judge=False,
        exclude_paper_ids={"p-source"},
    )
    assert all(item.paper_id != "p-source" for item in resp.items)
    assert len(resp.items) == 1
    assert resp.items[0].paper_id == "p-other"


def test_similar_work_always_excludes_source_paper(
    db_session, monkeypatch, tmp_path
) -> None:
    """即使调用方未传入排除项，find_similar_work 也必须将源论文加入排除集合。"""
    fake = _patch_service_deps(
        monkeypatch,
        hits=[{"chunk_id": "c1", "workspace_id": "ws-1", "paper_id": "p-src",
               "section": "M", "text": "t", "score": 0.9, "source_artifact_id": "a1", "chunk_index": 1}],
    )
    monkeypatch.setattr(
        "app.core.config.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )
    workspace_id = str(uuid4())
    paper_id = str(uuid4())
    db_session.add(
        Workspace(id=workspace_id, name="Retrieval Contract", is_deleted=False)
    )
    paper = Paper(
        id=paper_id,
        workspace_id=workspace_id,
        title="Source paper",
        authors=[],
        source="manual",
        is_deleted=False,
    )
    db_session.add(paper)
    db_session.flush()
    payload = "\n".join(
        json.dumps({
            "chunk_id": f"src-{i}",
            "workspace_id": workspace_id,
            "paper_id": paper_id,
            "source_artifact_id": "art-1",
            "chunk_index": i,
            "text": "source chunk",
            "start_char": 0,
            "end_char": 10,
        })
        for i in range(3)
    )
    artifact = ArtifactService(db_session).save_upload(
        workspace_id=workspace_id,
        filename=f"{paper_id}_chunks.jsonl",
        content=payload.encode("utf-8"),
        mime_type="application/jsonl",
        kind="chunk_index",
    )
    paper.chunk_index_artifact_id = artifact.id
    db_session.commit()

    resp = service.find_similar_work(
        workspace_id,
        paper_id,
        top_k=5,
        db=db_session,
        use_reranker=False,
    )
    assert paper_id in (fake.calls[0]["exclude_paper_ids"] or set())
    assert resp.filters_applied["excluded_paper_ids"] == [paper_id]


def test_semantic_search_forwards_exclude(monkeypatch) -> None:
    fake = _patch_service_deps(monkeypatch, hits=[])
    resp = service.semantic_search("ws-1", "query", top_k=5, exclude_paper_ids={"p-x"})
    assert fake.calls[0]["exclude_paper_ids"] == {"p-x"}
    assert resp.filters_applied["excluded_paper_ids"] == ["p-x"]
# 空结果属于“succeeded”（没有命中），而不是“failed”——没有反例不能报告为系统失败。
    assert resp.status == "succeeded"
    assert resp.items == []
