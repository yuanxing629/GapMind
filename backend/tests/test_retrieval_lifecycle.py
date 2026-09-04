"""索引生命周期与降级路径测试（RG-6 / V3）。

phase3_smoke_validation_and_next_plan.md §6 V3 的 contract 项：

  1. 同一论文重复 embedding 不会产生重复 chunk。
  2. Chunk version 变化 → 完整删除旧向量。
  3. Paper 软删除后 → Retrieval 不再返回该论文。
  4. Workspace archive / soft-delete 策略与 retrieval 一致。
  5. Embedding / Milvus / reranker 失败 → 返回明确的 failed/degraded 状态，不会静默回退为
     "empty success"。
  6. Task 和 Paper 投影状态不会虚报真实 Milvus 状态。
  7. 全新 DB 端到端流程（migration → upload → parse → extract → index → search）。

大多数测试 mock Milvus（CI 中没有 live Milvus 实例），并使用 SQLite fixture 保存 Paper /
Task / Workspace 状态。第 (3) 项的跨 domain 删除传播通过 mock
milvus_client.delete_by_paper 并断言 paper soft_delete 时调用它来测试。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domains.artifact.service import ArtifactService
from app.domains.paper.models import Paper
from app.domains.paper.service import PaperService
from app.domains.retrieval import milvus_client, service as retrieval_service
from app.domains.workspace.models import Workspace


# ==================================================================
# Fixtures：测试夹具
# ==================================================================


@pytest.fixture
def fake_milvus(_stub_milvus) -> MagicMock:
    """重新导出 conftest 提供的 Milvus 替身，便于测试主体显式引用。

    返回同一个对象（而不是新的 MagicMock），这样
    ``fake_milvus.delete_by_paper.assert_called_once_with(...)`` 等断言，
    才能看到 paper.service / retrieval.service 通过其替换后的模块属性触发的调用。
    """
    return _stub_milvus


@pytest.fixture
def fake_embedding(monkeypatch) -> MagicMock:
    fake = MagicMock(name="embedding")
    fake.model = "fake-emb"
    fake.dim = 4
    fake.embed_one.return_value = [0.1, 0.2, 0.3, 0.4]
# 每条输入文本返回一个 embedding（pymilvus 契约）。
    def _fake_embed(texts):
        from types import SimpleNamespace
        return SimpleNamespace(embeddings=[[0.1, 0.2, 0.3, 0.4] for _ in texts])

    fake.embed_texts.side_effect = _fake_embed
    monkeypatch.setattr(retrieval_service, "get_embedding_gateway", lambda: fake)
    return fake


def _workspace(db: Session, *, archived: bool = False) -> Workspace:
    import uuid as _uuid
    ws = Workspace(id=str(_uuid.uuid4()), name="Lifecycle Test", is_deleted=False, is_archived=archived)
    db.add(ws)
    db.commit()
    return ws


def _paper(
    db: Session,
    ws_id: str,
    *,
    chunk_count: int = 0,
    parse_status: str = "parsed",
) -> Paper:
    import uuid as _uuid
    paper = Paper(
        id=str(_uuid.uuid4()),
        workspace_id=ws_id,
        title="Lifecycle Test Paper",
        authors=[],
        source="manual",
        parse_status=parse_status,
        chunk_count=chunk_count,
        is_deleted=False,
    )
    db.add(paper)
    db.commit()
    return paper


def _write_chunks_jsonl(db: Session, paper: Paper, n: int):
    """为合成论文创建规范的 chunk_index Artifact。"""
    payload = "\n".join(
        json.dumps({
            "chunk_id": f"{paper.id}-c{i}",
            "workspace_id": paper.workspace_id,
            "paper_id": paper.id,
            "source_artifact_id": "art-1",
            "chunk_index": i,
            "text": f"chunk {i}",
            "start_char": 0,
            "end_char": 10,
        })
        for i in range(n)
    )
    artifact = ArtifactService(db).save_upload(
        workspace_id=paper.workspace_id,
        filename=f"{paper.id}_chunks.jsonl",
        content=payload.encode("utf-8"),
        mime_type="application/jsonl",
        kind="chunk_index",
    )
    paper.chunk_index_artifact_id = artifact.id
    db.commit()
    return artifact


def test_chunk_records_resolve_from_storage_artifact(
    db_session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path / "storage"))
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)
    _write_chunks_jsonl(db_session, paper, n=2)

    chunk = retrieval_service.find_chunk_record(
        ws.id,
        paper.id,
        f"{paper.id}-c1",
        db=db_session,
    )
    assert chunk is not None
    assert chunk.start_char == 0
    assert chunk.end_char == 10

# 没有指针的历史行仍可通过数据库元数据解析出精确的 workspace 级 chunk_index 文件名。
    paper.chunk_index_artifact_id = None
    db_session.commit()
    assert retrieval_service.find_chunk_record(
        ws.id,
        paper.id,
        f"{paper.id}-c1",
        db=db_session,
    ) is not None
    assert retrieval_service.find_chunk_record(
        str(uuid4()),
        paper.id,
        f"{paper.id}-c1",
        db=db_session,
    ) is None


# ==================================================================
# 1. 幂等索引
# ==================================================================


def test_index_paper_chunks_idempotent_skips_existing(
    db_session, fake_milvus, fake_embedding, tmp_path, monkeypatch
) -> None:
    """同一论文在未启用 force_reindex 时重复索引，第二次调用
    get_existing_chunk_ids，并且不插入新的分块。"""
    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path / "storage"))
# Conftest 的替身默认返回 insert_chunks=0；通过返回批次大小模拟真实 Milvus。
    fake_milvus.insert_chunks.side_effect = lambda batch: len(batch)
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)
    _write_chunks_jsonl(db_session, paper, n=3)

# 第一次索引：尚无已存在内容 → 写入全部 3 个
    fake_milvus.get_existing_chunk_ids.return_value = set()
    r1 = retrieval_service.index_paper_chunks(ws.id, paper.id, db=db_session)
    assert r1.indexed_count == 3
    assert r1.skipped_count == 0

# 第二次索引：报告全部 3 个已存在 → 跳过
    fake_milvus.get_existing_chunk_ids.return_value = {
        f"{paper.id}-c0", f"{paper.id}-c1", f"{paper.id}-c2",
    }
    r2 = retrieval_service.index_paper_chunks(ws.id, paper.id, db=db_session)
    assert r2.indexed_count == 0
    assert r2.skipped_count == 3
    assert r2.error is None


# ==================================================================
# 2. 强制重建索引会在重新写入前删除旧向量
# ==================================================================


def test_index_force_reindex_calls_delete_by_paper_first(
    db_session, fake_milvus, fake_embedding, tmp_path, monkeypatch
) -> None:
    """force_reindex=True 必须调用 delete_by_paper，避免上次解析的旧分块残留。

    如果某个 chunk_id 在多次解析间仍然存在，则重新插入它（chunk_version 变化表示内容已更新）。
    """
    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path / "storage"))
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)
    _write_chunks_jsonl(db_session, paper, n=3)

    retrieval_service.index_paper_chunks(ws.id, paper.id, db=db_session, force_reindex=True)

    fake_milvus.delete_by_paper.assert_called_once_with(
        paper.id,
        workspace_id=ws.id,
    )
# delete 发生在 insert 之前，验证调用顺序。
    call_order = [c[0] for c in fake_milvus.mock_calls]
    assert "delete_by_paper" in call_order
    assert "insert_chunks" in call_order
    assert call_order.index("delete_by_paper") < call_order.index("insert_chunks")


# ==================================================================
# 3. 软删除论文 -> 没有 Retrieval 命中
# ==================================================================


def test_paper_soft_delete_propagates_to_milvus(
    db_session, monkeypatch
) -> None:
    """当 paper.is_deleted 变为 True 时，必须删除对应的 Milvus 向量，
    使后续检索不会再返回该论文。

    这是跨领域传播契约——论文生命周期事件必须传播到搜索索引。
    本测试通过替换 paper.service 持有的 ``milvus_client`` 模块属性来断言这一点。
    """
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)

    fake_milvus = MagicMock(name="milvus_client")
    import app.domains.paper.service as paper_service_module
    monkeypatch.setattr(paper_service_module.milvus_client, "delete_by_paper", fake_milvus.delete_by_paper)

    PaperService(db_session).soft_delete(paper.id)

    fake_milvus.delete_by_paper.assert_called_once_with(
        paper.id,
        workspace_id=ws.id,
    )


def test_soft_deleted_paper_excluded_from_retrieval_via_milvus_deletion(
    db_session, fake_milvus, fake_embedding, tmp_path, monkeypatch
) -> None:
    """端到端流程：索引论文、软删除论文（同时从 Milvus 删除），
    然后验证检索不会返回该论文的内容。

    conftest 中自动使用的 _stub_milvus fixture 提供共享 MagicMock；这里显式传入的
    fake_milvus 参数就是同一个对象（它是函数级自动 fixture，fake_milvus 只是为了便于
    测试重新导出它）。因此本测试直接对共享替身进行断言。
    """
    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path / "storage"))
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)
    _write_chunks_jsonl(db_session, paper, n=2)

# 索引。
    fake_milvus.get_existing_chunk_ids.return_value = set()
    retrieval_service.index_paper_chunks(ws.id, paper.id, db=db_session)
    assert fake_milvus.insert_chunks.call_count >= 1

# 软删除 -> 应同步到 Milvus。
    PaperService(db_session).soft_delete(paper.id)
    assert fake_milvus.delete_by_paper.call_count == 1

# 现在检索不返回命中，因为 Milvus 中该论文已为空。
    fake_milvus.search.return_value = []
    resp = retrieval_service.semantic_search(ws.id, "anything")
    assert resp.total == 0
    assert resp.status == "succeeded"  # empty retrieval is NOT a failure


# ==================================================================
# 4. Workspace 归档/软删除契约
# ==================================================================


def test_workspace_is_archived_does_not_affect_retrieval(
    db_session, fake_milvus, fake_embedding
) -> None:
    """归档是非破坏性标记（保留工作区以便查看历史）。

    对已归档工作区的检索仍然可用，用户可以查询历史记录。
    （软删除才是破坏性变体。）"""
    ws = _workspace(db_session, archived=True)

    fake_milvus.search.return_value = [
        {"chunk_id": "c1", "workspace_id": ws.id, "paper_id": "p-A",
         "section": "M", "text": "t", "score": 0.9,
         "source_artifact_id": "a1", "chunk_index": 1},
    ]

    resp = retrieval_service.semantic_search(ws.id, "query")
    assert resp.status == "succeeded"
    assert resp.total == 1
# 过滤器仍将查询固定在 workspace 作用域内。
    fake_milvus.search.assert_called_once()
# workspace_id 是 milvus_client.search 的第一个位置参数。
    args, kwargs = fake_milvus.search.call_args
    assert args[1] == ws.id  # (query_vector, workspace_id, top_k=...)


# ==================================================================
# 5. 失败路径：显式状态，不静默回退
# ==================================================================


class _FakeEmbeddingBoom:
    """embed_one 抛出异常时，检索必须返回清晰的失败状态，
    不能假装搜索成功并返回空结果。"""

    model = "fake"
    dim = 4

    def embed_one(self, text: str):
        raise RuntimeError("upstream embedding provider 503")

    def embed_texts(self, texts):
        raise RuntimeError("upstream embedding provider 503")


class _FakeMilvusBoom:
    def search(self, *args, **kwargs):
        raise RuntimeError("milvus connection refused")


def test_semantic_search_status_failed_on_embedding_error(monkeypatch) -> None:
    monkeypatch.setattr(retrieval_service, "get_embedding_gateway", lambda: _FakeEmbeddingBoom())
    monkeypatch.setattr(retrieval_service, "milvus_client", _FakeMilvusBoom())

    resp = retrieval_service.semantic_search("ws-1", "query", top_k=5)
    assert resp.status == "failed"
    assert resp.items == []
    assert resp.error is not None and "embedding" in resp.error.lower()
    assert resp.diagnostic_code == "embedding_unavailable"


def test_semantic_search_status_failed_on_milvus_error(monkeypatch) -> None:
    class _OkEmbedding:
        model = "fake"
        dim = 4
        def embed_one(self, text): return [0.1] * 4
        def embed_texts(self, texts):
            from types import SimpleNamespace
            return SimpleNamespace(embeddings=[[0.1] * 4] * len(texts))

    monkeypatch.setattr(retrieval_service, "get_embedding_gateway", lambda: _OkEmbedding())
    monkeypatch.setattr(retrieval_service, "milvus_client", _FakeMilvusBoom())

    resp = retrieval_service.semantic_search("ws-1", "query", top_k=5)
    assert resp.status == "failed"
    assert "milvus" in (resp.error or "").lower()
    assert resp.diagnostic_code == "milvus_unavailable"


def test_semantic_search_status_failed_on_unloaded_collection(monkeypatch) -> None:
    class _OkEmbedding:
        def embed_one(self, text): return [0.1] * 4

    class _UnloadedCollection:
        def search(self, *args, **kwargs):
            raise RuntimeError("collection not loaded")

    monkeypatch.setattr(retrieval_service, "get_embedding_gateway", lambda: _OkEmbedding())
    monkeypatch.setattr(retrieval_service, "milvus_client", _UnloadedCollection())

    resp = retrieval_service.semantic_search("ws-1", "query", top_k=5)
    assert resp.status == "failed"
    assert resp.diagnostic_code == "collection_unloaded"
    assert "重建索引" in (resp.error or "")


def test_counter_evidence_status_failed_on_milvus_error(monkeypatch) -> None:
    """反证检索失败时不能静默返回空的成功结果。

    用户需要知道系统是“未能找到内容”，还是“找到的内容中没有结果”。"""
    class _OkEmbedding:
        model = "fake"
        dim = 4
        def embed_one(self, text): return [0.1] * 4
        def embed_texts(self, texts):
            from types import SimpleNamespace
            return SimpleNamespace(embeddings=[[0.1] * 4] * len(texts))

    monkeypatch.setattr(retrieval_service, "get_embedding_gateway", lambda: _OkEmbedding())
    monkeypatch.setattr(retrieval_service, "milvus_client", _FakeMilvusBoom())

    resp = retrieval_service.find_counter_evidence(
        "ws-1", "claim", top_k=10,
        use_reranker=False, use_judge=False,
    )
    assert resp.status == "failed"
    assert resp.total == 0
    assert resp.diagnostic_code == "milvus_unavailable"


def test_reranker_failure_falls_back_to_score_only(
    monkeypatch, fake_milvus, fake_embedding
) -> None:
    """重排器采用尽力而为策略：失败时降级为按向量分数排序。

    这是已记录的行为——semantic_search 使用现有信号返回 degraded 状态。
    （反证检索也采用相同策略。）"""
    class _BoomReranker:
        def rerank(self, query, documents, *, top_n):
            raise RuntimeError("reranker 502")

    fake_milvus.search.return_value = [
        {"chunk_id": f"c{i}", "workspace_id": "ws-1", "paper_id": "p-A",
         "section": "M", "text": f"t{i}", "score": 0.9 - i * 0.1,
         "source_artifact_id": "a1", "chunk_index": i}
        for i in range(3)
    ]
    monkeypatch.setattr(retrieval_service, "get_reranker_gateway", lambda: _BoomReranker())

    resp = retrieval_service.semantic_search("ws-1", "query", top_k=3, use_reranker=True)
    assert resp.status == "degraded"
    assert resp.diagnostic_code == "reranker_degraded"
# 我们获得了可用信号，而不是伪造的失败。
    assert resp.total == 3
    assert all(item.retrieval_stage == "candidate_recall" for item in resp.items)


def test_semantic_search_can_diversify_reranked_chunks_by_paper(
    monkeypatch, fake_milvus, fake_embedding
) -> None:
    """Chat 回答生成不应让同一篇论文占据所有证据槽位。"""

    class _OrderedReranker:
        def __init__(self):
            self.top_n = None

        def rerank(self, query, documents, *, top_n):
            self.top_n = top_n
            return type("Result", (), {
                "hits": [
                    type("Hit", (), {"index": index, "relevance_score": 1.0 - index * 0.01})()
                    for index in range(len(documents))
                ]
            })()

    reranker = _OrderedReranker()
    fake_milvus.search.return_value = [
        {"chunk_id": "a-1", "workspace_id": "ws-1", "paper_id": "paper-a", "section": "Method", "text": "a1", "score": 0.99, "source_artifact_id": "art-a", "chunk_index": 1},
        {"chunk_id": "a-2", "workspace_id": "ws-1", "paper_id": "paper-a", "section": "Method", "text": "a2", "score": 0.98, "source_artifact_id": "art-a", "chunk_index": 2},
        {"chunk_id": "a-3", "workspace_id": "ws-1", "paper_id": "paper-a", "section": "Result", "text": "a3", "score": 0.97, "source_artifact_id": "art-a", "chunk_index": 3},
        {"chunk_id": "b-1", "workspace_id": "ws-1", "paper_id": "paper-b", "section": "Method", "text": "b1", "score": 0.96, "source_artifact_id": "art-b", "chunk_index": 1},
        {"chunk_id": "c-1", "workspace_id": "ws-1", "paper_id": "paper-c", "section": "Method", "text": "c1", "score": 0.95, "source_artifact_id": "art-c", "chunk_index": 1},
    ]
    monkeypatch.setattr(retrieval_service, "get_reranker_gateway", lambda: reranker)

    response = retrieval_service.semantic_search(
        "ws-1",
        "query",
        top_k=3,
        diversify_by_paper=True,
    )

    assert response.status == "succeeded"
    assert [item.paper_id for item in response.items] == ["paper-a", "paper-b", "paper-c"]
    assert response.filters_applied["diversify_by_paper"] is True
    assert reranker.top_n == 9


# ==================================================================
# 6. Paper.chunk_count 与 Milvus 状态——不能错误投影
# ==================================================================


def test_paper_chunk_count_matches_indexed_chunks(
    db_session, fake_milvus, fake_embedding, tmp_path, monkeypatch
) -> None:
    """Task 行和 Paper.chunk_count 必须反映实际索引数量，不能使用上次索引运行的旧计数。

    本测试断言索引器报告正确数量。流水线
    负责根据 index_paper_chunks 更新 paper.chunk_count 的 layer 已由 test_parse_pipeline.py
    端到端集成测试单独验证；本测试锁定 indexer 的 contract。"""
    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path / "storage"))
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)
    _write_chunks_jsonl(db_session, paper, n=4)

    fake_milvus.get_existing_chunk_ids.return_value = set()
    fake_milvus.insert_chunks.return_value = 4  # batch returns count

    result = retrieval_service.index_paper_chunks(ws.id, paper.id, db=db_session)
    assert result.total_chunks == 4  # chunk_index Artifact read
    assert result.indexed_count == 4  # Milvus insert returned count
    assert result.skipped_count == 0


def test_partial_insertion_reported_as_partial(
    db_session, fake_milvus, fake_embedding, tmp_path, monkeypatch
) -> None:
    """如果 Milvus insert_chunks 返回的数量少于批次大小，索引器必须如实报告。

    这是 Task 行准确性的基础（Task 报告部分完成，而不是 100% 完成）。"""
    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path / "storage"))
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)
    _write_chunks_jsonl(db_session, paper, n=10)

    fake_milvus.get_existing_chunk_ids.return_value = set()
    # 单个批次（10 个分块 < batch_size=100）：插入 10 个中的 8 个。
    fake_milvus.insert_chunks.side_effect = lambda batch: 8

    result = retrieval_service.index_paper_chunks(ws.id, paper.id, db=db_session)
    assert result.total_chunks == 10
    assert result.indexed_count == 8  # 如实记录数量
    # 调用方可以比较 indexed_count 和 total_chunks 来检测部分完成。


# ==================================================================
# 7. 全新数据库端到端测试（轻量契约检查）
# ==================================================================


def test_indexer_writes_consistent_records_to_milvus(
    db_session, fake_milvus, fake_embedding, tmp_path, monkeypatch
) -> None:
    """完整索引的记录结构契约：传给 Milvus 的每条记录都必须包含
    workspace_id、paper_id、chunk_id、section、text 和 embedding。
    调用该流程的上层依赖每个字段都正确写入向量库。"""
    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path / "storage"))
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)
    _write_chunks_jsonl(db_session, paper, n=2)

    fake_milvus.get_existing_chunk_ids.return_value = set()
    retrieval_service.index_paper_chunks(ws.id, paper.id, db=db_session)

# 收集所有批次传给 insert_chunks 的记录。
    all_records: list[dict] = []
    for call in fake_milvus.insert_chunks.call_args_list:
        all_records.extend(call.args[0])

    assert len(all_records) == 2
    for record in all_records:
        assert record["workspace_id"] == ws.id
        assert record["paper_id"] == paper.id
        assert record["chunk_id"].startswith(paper.id + "-c")
        assert record["section"]  # default "Unknown" if chunk had none
        assert isinstance(record["embedding"], list)
        assert len(record["embedding"]) == 4  # matches fake_embedding.dim


# ==================================================================
# 辅助场景：paper.soft_delete 失败路径——Milvus 异常不能静默让论文向量继续留在索引中
# ==================================================================


def test_paper_soft_delete_records_failure_when_milvus_throws(
    db_session, monkeypatch
) -> None:
    """如果 Milvus delete_by_paper 失败，paper soft_delete 必须抛出异常，
    使 API 返回 5xx（不能静默返回 200）。数据库层的 is_deleted 翻转会在
    Milvus 调用前提交，因而可能留下已知的不一致状态，API 调用方可以通过后续 GET 检测到。
    这里选择显式失败，而不是静默接受不一致。
    """
    fake_milvus = MagicMock(name="milvus_client")
    fake_milvus.delete_by_paper.side_effect = RuntimeError("milvus unreachable")
    import app.domains.paper.service as paper_service_module
    monkeypatch.setattr(paper_service_module.milvus_client, "delete_by_paper", fake_milvus.delete_by_paper)

    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)

    with pytest.raises(RuntimeError, match="milvus unreachable"):
        PaperService(db_session).soft_delete(paper.id)

# 数据库侧标记确实已翻转（已记录；后续 reconcile 可以修复）。
    db_session.expire_all()
    fresh = db_session.get(Paper, paper.id)
    assert fresh is not None
    assert fresh.is_deleted is True
