"""论文分块向量的 Milvus 客户端包装器。

管理 `gapmind_paper_chunks` collection，包括创建、写入、搜索和清理。所有操作都限定在
workspace 范围内。
"""

from __future__ import annotations

from typing import Any

from pymilvus import (
    CollectionSchema,
    DataType,
    FieldSchema,
    MilvusClient,
)

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

COLLECTION_NAME = f"{settings.milvus_collection_prefix}paper_chunks"
EMBEDDING_DIM = settings.embedding_dimension  # 1024 for BGE-M3

# HNSW 索引参数（适用于少于 1M 向量时的召回率/速度折中）
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 256
HNSW_EF_SEARCH = 128

_client: MilvusClient | None = None


def get_milvus_client() -> MilvusClient:
    """Milvus 连接单例。"""
    global _client
    if _client is None:
        uri = f"http://{settings.milvus_host}:{settings.milvus_port}"
        _client = MilvusClient(uri=uri)
        logger.info("milvus.connected", uri=uri)
    return _client


def ensure_collection() -> None:
    """如果不存在则创建 paper_chunks collection 和索引，然后加载。"""
    client = get_milvus_client()

    if client.has_collection(COLLECTION_NAME):
        client.load_collection(COLLECTION_NAME)
        return

    fields = [
        FieldSchema(
            name="chunk_id",
            dtype=DataType.VARCHAR,
            max_length=36,
            is_primary=True,
        ),
        FieldSchema(
            name="workspace_id",
            dtype=DataType.VARCHAR,
            max_length=36,
        ),
        FieldSchema(
            name="paper_id",
            dtype=DataType.VARCHAR,
            max_length=36,
        ),
        FieldSchema(
            name="source_artifact_id",
            dtype=DataType.VARCHAR,
            max_length=36,
        ),
        FieldSchema(
            name="chunk_index",
            dtype=DataType.INT64,
        ),
        FieldSchema(
            name="section",
            dtype=DataType.VARCHAR,
            max_length=128,
        ),
        FieldSchema(
            name="text",
            dtype=DataType.VARCHAR,
            max_length=8192,
        ),
        FieldSchema(
            name="tokens_estimate",
            dtype=DataType.INT64,
        ),
        FieldSchema(
            name="embedding",
            dtype=DataType.FLOAT_VECTOR,
            dim=EMBEDDING_DIM,
        ),
    ]

    schema = CollectionSchema(
        fields=fields,
        description="Paper chunks with BGE-M3 embeddings for semantic retrieval",
        enable_dynamic_field=False,
    )

    client.create_collection(
        collection_name=COLLECTION_NAME,
        schema=schema,
    )

# 通过 IndexParams 构建全部索引（pymilvus >= 2.4 API）
    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="embedding",
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": HNSW_M, "efConstruction": HNSW_EF_CONSTRUCTION},
    )
    index_params.add_index(
        field_name="workspace_id",
        index_type="INVERTED",
    )
    index_params.add_index(
        field_name="paper_id",
        index_type="INVERTED",
    )
    client.create_index(
        collection_name=COLLECTION_NAME,
        index_params=index_params,
    )
    client.load_collection(COLLECTION_NAME)

    logger.info("milvus.collection_created", collection=COLLECTION_NAME)


def insert_chunks(records: list[dict[str, Any]]) -> int:
    """将分块记录写入 Milvus，返回写入数量。"""
    if not records:
        return 0
    client = get_milvus_client()
    ensure_collection()
    result = client.insert(collection_name=COLLECTION_NAME, data=records)
    count = result.get("insert_count", len(records))
    logger.info("milvus.inserted", collection=COLLECTION_NAME, count=count)
    return count


def search(
    query_vector: list[float],
    workspace_id: str,
    top_k: int = 10,
    *,
    paper_id: str | None = None,
    exclude_paper_ids: set[str] | None = None,
    section: str | None = None,
) -> list[dict[str, Any]]:
    """在 workspace 内执行向量相似度搜索。

    ``exclude_paper_ids`` 会下推到 Milvus 的 ``filter`` 表达式
    （``paper_id not in [...]``），因此被排除的论文不会进入召回池，而不是只在排序后
    过滤。这是 counter-evidence 的正确性要求：claim 的来源论文必须在*召回时*排除，
    否则其自身 chunk 可能挤掉真正的反证。

    返回包含字段和 score 的 dict 列表，并按相关性降序排列。
    """
    client = get_milvus_client()
    ensure_collection()

# 构建过滤表达式
    filters = [f'workspace_id == "{workspace_id}"']
    if paper_id:
        filters.append(f'paper_id == "{paper_id}"')
    if exclude_paper_ids:
        quoted = ", ".join(f'"{pid}"' for pid in sorted(exclude_paper_ids))
        filters.append(f"paper_id not in [{quoted}]")
    if section:
        filters.append(f'section == "{section}"')
    filter_expr = " and ".join(filters)

    results = client.search(
        collection_name=COLLECTION_NAME,
        data=[query_vector],
        limit=top_k,
        filter=filter_expr,
        output_fields=[
            "chunk_id",
            "workspace_id",
            "paper_id",
            "source_artifact_id",
            "chunk_index",
            "section",
            "text",
            "tokens_estimate",
        ],
        search_params={"metric_type": "COSINE", "params": {"ef": HNSW_EF_SEARCH}},
    )

    hits: list[dict[str, Any]] = []
    for hit in results[0]:
        entity = hit.get("entity", {})
        entity["score"] = hit.get("distance", 0.0)
        entity["chunk_id"] = hit.get("id", "")
        hits.append(entity)
    return hits


def count_by_workspace(workspace_id: str) -> int:
    """统计 workspace 中已索引的分块数量。"""
    client = get_milvus_client()
    ensure_collection()
    result = client.query(
        collection_name=COLLECTION_NAME,
        filter=f'workspace_id == "{workspace_id}"',
        output_fields=["count(*)"],
    )
    if result:
        return int(result[0].get("count(*)", 0))
    return 0


def get_existing_chunk_ids(
    paper_id: str,
    *,
    workspace_id: str | None = None,
) -> set[str]:
    """获取一篇论文的已索引分块 ID，可选按 workspace 限定。"""
    client = get_milvus_client()
    ensure_collection()
    filters = [f'paper_id == "{paper_id}"']
    if workspace_id:
        filters.insert(0, f'workspace_id == "{workspace_id}"')
    results = client.query(
        collection_name=COLLECTION_NAME,
        filter=" and ".join(filters),
        output_fields=["chunk_id"],
        limit=16384,
    )
    return {r["chunk_id"] for r in results}


def delete_by_paper(paper_id: str, *, workspace_id: str | None = None) -> None:
    """移除一篇论文的向量，可选按 workspace 限定。"""
    client = get_milvus_client()
    ensure_collection()
    filters = [f'paper_id == "{paper_id}"']
    if workspace_id:
        filters.insert(0, f'workspace_id == "{workspace_id}"')
    client.delete(
        collection_name=COLLECTION_NAME,
        filter=" and ".join(filters),
    )
    logger.info("milvus.deleted_by_paper", paper_id=paper_id)


def ping() -> bool:
    """检查 Milvus 连通性。"""
    try:
        client = get_milvus_client()
        client.list_collections()
        return True
    except Exception as e:
        logger.warning("milvus.ping_failed", error=str(e))
        return False
