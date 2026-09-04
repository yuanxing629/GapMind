"""Milvus client wrapper for paper chunk vectors.

Manages the `gapmind_paper_chunks` collection: creation, insertion,
search, and cleanup. All operations are workspace-scoped.
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

# HNSW index params (good recall/speed tradeoff for <1M vectors)
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 256
HNSW_EF_SEARCH = 128

_client: MilvusClient | None = None


def get_milvus_client() -> MilvusClient:
    """Singleton Milvus connection."""
    global _client
    if _client is None:
        uri = f"http://{settings.milvus_host}:{settings.milvus_port}"
        _client = MilvusClient(uri=uri)
        logger.info("milvus.connected", uri=uri)
    return _client


def ensure_collection() -> None:
    """Create the paper_chunks collection + indexes if not exists, then load."""
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

    # Build all indexes via IndexParams (pymilvus >= 2.4 API)
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
    """Insert chunk records into Milvus. Returns count inserted."""
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
    """Vector similarity search within a workspace.

    ``exclude_paper_ids`` is pushed down into the Milvus ``filter``
    expression (``paper_id not in [...]``) so excluded papers never enter
    the recall pool at all — not merely filtered after ranking. This is a
    correctness requirement for counter-evidence: a claim's source paper
    must be excluded *at recall time*, or its own chunks would crowd out
    genuinely countering evidence.

    Returns list of dicts with fields + score, sorted by relevance desc.
    """
    client = get_milvus_client()
    ensure_collection()

    # Build filter expression
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
    """Count indexed chunks for a workspace."""
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
    """Get indexed chunk IDs for one paper, optionally workspace-scoped."""
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
    """Remove vectors for one paper, optionally constrained to its workspace."""
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
    """Check Milvus connectivity."""
    try:
        client = get_milvus_client()
        client.list_collections()
        return True
    except Exception as e:
        logger.warning("milvus.ping_failed", error=str(e))
        return False
