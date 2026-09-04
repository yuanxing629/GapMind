"""PostgreSQL-first 的有界 GraphRAG 投影。

本模块刻意不依赖向量库或 Neo4j。Dense retrieval 提供 paper/chunk seed；此只读
投影只沿有界、按 workspace 限定的关系扩展，并从 PostgreSQL 重新读取 evidence
span。结果是诊断上下文，不是持久化事实。
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domains.artifact.models import Artifact
from app.domains.knowledge.models import (
    CanonicalEntity,
    EvidenceSpan,
    KnowledgeItem,
    KnowledgeRelation,
    PaperMention,
)
from app.domains.knowledge.schemas import (
    GraphRAGEdgeRead,
    GraphRAGEvidenceRead,
    GraphRAGNodeRead,
    GraphRAGPathRead,
    GraphRAGSeedRead,
)
from app.domains.paper.models import Paper
from app.domains.retrieval.schemas import RetrievalResultItem

logger = get_logger(__name__)

GRAPH_RAG_PROJECTION_VERSION = "sql_graph_v1"
WORKSPACE_ENTITY_TYPES = frozenset({"method", "task", "dataset"})
REJECTED_STATUSES = frozenset({"rejected", "invalidated", "deprecated"})
CONFIRMED_STATUSES = frozenset({"human_confirmed", "experiment_validated"})
SAFE_RELATION_TYPES = frozenset(
    {
        "supports",
        "qualifies",
        "contradicts",
        "evaluates_on",
        "compares_with",
        "extends",
        "related_to",
    }
)

# Graph 路径是诊断候选，但路径展示的证据仍必须与当前问题存在可度量的关联。
# 首轮保持确定性和本地执行：dense retrieval 已经完成 query embedding，
# 因此该过滤器可避免 shadow 模式下再次调用 provider。
GRAPH_RAG_MIN_EVIDENCE_RELEVANCE = 0.15
_QUERY_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "be", "been", "being", "by", "do", "does",
        "for", "from", "how", "in", "is", "it", "its", "of", "on", "or", "that",
        "the", "these", "this", "those", "to", "was", "were", "what", "when",
        "where", "which", "who", "why", "with", "use", "used", "uses", "using",
        "mention", "mentions", "mentioned", "compare", "comparison", "evaluate",
        "evaluates", "evaluation", "show", "shows", "find", "finds", "paper", "papers",
        "study", "studies", "请", "请问", "什么", "哪些", "哪个", "是否", "如何", "为什么",
        "怎么", "这个", "这", "那个", "的", "了", "吗", "呢", "与", "和", "及", "在",
        "中", "对", "关于", "可以", "能否", "提到", "使用", "采用", "介绍", "比较", "分析",
        "评估", "找到", "给出", "说明", "研究", "论文",
    }
)
_ASCII_TERM_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
_QUERY_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9]{2,}\b")
_CJK_RUN_RE = re.compile(r"[\u3400-\u9fff]+")


def _compact_text(value: str | None) -> str:
    """标准化文本，用于安全的精确或子串比较。"""

    normalized = unicodedata.normalize("NFKC", value or "").lower()
    return "".join(char for char in normalized if char.isalnum())


def _text_terms(value: str | None) -> set[str]:
    """提取稳定的英文词项以及 CJK 二元组/三元组。"""

    normalized = unicodedata.normalize("NFKC", value or "").lower()
    terms = {
        term
        for term in _ASCII_TERM_RE.findall(normalized)
        if term not in _QUERY_STOPWORDS and len(term) >= 2
    }
    for run in _CJK_RUN_RE.findall(normalized):
        if len(run) == 1:
            terms.add(run)
            continue
        for width in (2, 3):
            terms.update(run[index : index + width] for index in range(len(run) - width + 1))
    return {term for term in terms if term not in _QUERY_STOPWORDS}


def _evidence_relevance_score(
    span: EvidenceSpan,
    item: KnowledgeItem,
    query_text: str,
    dense_items: list[RetrievalResultItem],
) -> float:
    """评估 graph evidence span 对当前问题是否有用。

    span 出现在 dense hit 中并不充分：一个 chunk 可能包含多个不相关的公式或引用。
    使用与问题的确定性词法重叠，并为跨 paper 路径加入保守的 entity/acronym
    锚点。这里是相关性门槛，不代表 scientific truth。
    """

    if not query_text.strip():
        return 1.0

    span_text = _compact_text(span.text)
    if not span_text:
        return 0.0

    query_terms = _text_terms(query_text)
    evidence_terms = _text_terms(span.text)
    shared_terms = query_terms & evidence_terms
    # 单个“graph”或“method”等通用词不足以证明较长证据片段相关。
    # Acronym 和精确 entity 锚点在下方单独处理，以支持简短的跨语言问题。
    lexical_score = (
        len(shared_terms) / len(query_terms)
        if len(shared_terms) >= 2 and query_terms
        else 0.0
    )

    query_acronyms = {
        term.lower()
        for term in _QUERY_ACRONYM_RE.findall(query_text)
    }
    if query_acronyms & evidence_terms:
        lexical_score = max(lexical_score, 0.8)

    # 共享的 canonical entity 可能是相关论文进入路径的原因。
    # 要求该 entity 同时出现在 dense seed 和当前 EvidenceSpan 中；
    # 单独的 graph 边永远不足以作为依据。
    entity_name = _compact_text(item.canonical_name)
    if len(entity_name) >= 3 and entity_name in span_text:
        if any(
            dense.paper_id == span.paper_id
            and (not dense.artifact_id or not span.artifact_id or dense.artifact_id == span.artifact_id)
            and entity_name in _compact_text(dense.text)
            for dense in dense_items
        ):
            lexical_score = max(lexical_score, 0.6)

    return round(min(1.0, lexical_score), 4)


@dataclass
class BoundedGraphProjection:
    """供 Chat shadow 诊断使用的内部结果。

    ``nodes``/``edges`` 描述紧凑的 graph 拓扑。Item 和 EvidenceSpan 身份保留在
    路径及边的 provenance 载荷中。
    """

    projection_version: str
    seeds: list[GraphRAGSeedRead]
    paths: list[GraphRAGPathRead]
    nodes: list[GraphRAGNodeRead]
    edges: list[GraphRAGEdgeRead]
    evidence: list[GraphRAGEvidenceRead]
    supporting_paper_ids: list[str]
    supporting_evidence_ids: list[str]
    truncated: bool = False
    truncation_reason: str | None = None
    candidate_path_count: int = 0
    emitted_path_count: int = 0
    dropped_path_count: int = 0
    dropped_path_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def expanded_node_count(self) -> int:
        return len(self.nodes)

    @property
    def expanded_edge_count(self) -> int:
        return len(self.edges)


@dataclass(frozen=True)
class _PathCandidate:
    """等待确定性预算打包的已校验路径。"""

    path_id: str
    nodes: list[GraphRAGNodeRead]
    edges: list[GraphRAGEdgeRead]
    evidence: list[GraphRAGEvidenceRead]
    paper_ids: list[str]
    item_ids: list[str]
    review_status: str
    evidence_relevance_score: float
    dense_score: float
    confidence_score: float

    @property
    def node_count(self) -> int:
        return len({node.id for node in self.nodes})

    @property
    def edge_count(self) -> int:
        return len({edge.id for edge in self.edges})


def _review_status(statuses: Iterable[str | None]) -> str:
    values = {status for status in statuses if status}
    if any(status in REJECTED_STATUSES for status in values):
        return "rejected"
    if values and values.issubset(CONFIRMED_STATUSES):
        return "confirmed"
    return "candidate"


def _node_id(kind: str, raw_id: str) -> str:
    return f"{kind}:{raw_id}"


def _adapt_seeds(
    items: Iterable[RetrievalResultItem], workspace_id: str
) -> list[GraphRAGSeedRead]:
    seeds: list[GraphRAGSeedRead] = []
    seen: set[str] = set()
    for rank, item in enumerate(items, 1):
        if item.source_scope != "workspace" or not item.paper_id:
            continue
        if item.chunk_id:
            node_id = _node_id("chunk", item.chunk_id)
            if node_id not in seen:
                seeds.append(
                    GraphRAGSeedRead(
                        node_id=node_id,
                        node_kind="chunk",
                        workspace_id=workspace_id,
                        paper_id=item.paper_id,
                        chunk_id=item.chunk_id,
                        rank=rank,
                        score=item.score,
                    )
                )
                seen.add(node_id)
        else:
            node_id = _node_id("paper", item.paper_id)
            if node_id not in seen:
                seeds.append(
                    GraphRAGSeedRead(
                        node_id=node_id,
                        node_kind="paper",
                        workspace_id=workspace_id,
                        paper_id=item.paper_id,
                        rank=rank,
                        score=item.score,
                    )
                )
                seen.add(node_id)
    return seeds


def _evidence_read(
    span: EvidenceSpan,
    item: KnowledgeItem,
    *,
    paper: Paper,
    artifact: Artifact | None = None,
    query_relevance_score: float = 0.0,
) -> GraphRAGEvidenceRead:
    item_status = _review_status([item.status])
    return GraphRAGEvidenceRead(
        evidence_span_id=span.id,
        workspace_id=span.workspace_id,
        paper_id=paper.id,
        item_id=item.id,
        artifact_id=span.artifact_id or (artifact.id if artifact is not None else None),
        section=None,
        excerpt=(span.text or "").replace("\x00", "")[:4000],
        start_char=span.start_char,
        end_char=span.end_char,
        relation=span.relation,
        confidence=span.confidence,
        review_status=item_status,
        query_relevance_score=query_relevance_score,
    )


def build_bounded_projection(
    db: Session,
    *,
    workspace_id: str,
    dense_items: Iterable[RetrievalResultItem],
    request_id: str,
    query_text: str = "",
    max_hops: int = 2,
    node_limit: int = 32,
    edge_limit: int = 64,
) -> BoundedGraphProjection:
    """根据 dense seed 构建有界 graph，并重新检索 source span。

    SQL 被刻意拆分为多个有界阶段，而不是使用无界递归查询：seed papers ->
    entities -> 最多再扩展一跳 paper/entity。每次读取表都绑定 ``workspace_id``
    并过滤软删除标记，组装路径时还会再次校验外键身份。
    """

    dense_items = list(dense_items)
    max_hops = max(1, min(max_hops, 2))
    node_limit = max(1, min(node_limit, 200))
    edge_limit = max(1, min(edge_limit, 400))
    seeds = _adapt_seeds(dense_items, workspace_id)
    seed_paper_ids = {seed.paper_id for seed in seeds if seed.paper_id}
    evidence_required = bool(query_text.strip())

    if not seed_paper_ids:
        return BoundedGraphProjection(
            projection_version=GRAPH_RAG_PROJECTION_VERSION,
            seeds=[], paths=[], nodes=[], edges=[], evidence=[],
            supporting_paper_ids=[], supporting_evidence_ids=[],
            truncated=False,
        )

    seed_papers = list(
        db.scalars(
            select(Paper).where(
                Paper.workspace_id == workspace_id,
                Paper.is_deleted.is_(False),
                Paper.id.in_(seed_paper_ids),
            )
        )
    )
    paper_by_id = {paper.id: paper for paper in seed_papers}
    valid_seed_paper_ids = set(paper_by_id)
    seeds = [seed for seed in seeds if seed.paper_id in valid_seed_paper_ids]
    # 输出节点预算与 SQL 预取刻意分开。一个 dense seed 可能触达许多候选 mention/item，
    # 若在 ``node_limit * 4`` 处截断，相关 EvidenceSpan 可能在进入词法门控排序前就
    # 被丢弃。预取仍保持有界，但要足以覆盖当前 workspace 规模。
    prefetch_limit = min(512, max(256, node_limit * 16))
    query_truncated = False

    def fetch_bounded(statement, limit: int):
        """多取一行用于探测，从而确保截断意味着确实省略了数据。"""
        nonlocal query_truncated
        rows = list(db.scalars(statement.limit(limit + 1)))
        if len(rows) > limit:
            query_truncated = True
        return rows[:limit]

    # 阶段 1：只有论文局部的 entity item 和 mention 可以作为扩展 seed。
    item_rows = fetch_bounded(
        select(KnowledgeItem)
        .where(
            KnowledgeItem.workspace_id == workspace_id,
            KnowledgeItem.is_deleted.is_(False),
            KnowledgeItem.paper_id.in_(valid_seed_paper_ids),
            KnowledgeItem.type.in_(WORKSPACE_ENTITY_TYPES),
            KnowledgeItem.status.not_in(REJECTED_STATUSES),
            KnowledgeItem.canonical_entity_id.is_not(None),
        )
        .order_by(KnowledgeItem.confidence.desc(), KnowledgeItem.created_at.desc()),
        prefetch_limit,
    )
    mention_rows = fetch_bounded(
        select(PaperMention)
        .where(
            PaperMention.workspace_id == workspace_id,
            PaperMention.is_deleted.is_(False),
            PaperMention.paper_id.in_(valid_seed_paper_ids),
            PaperMention.status.not_in(REJECTED_STATUSES),
        )
        .order_by(PaperMention.confidence.desc(), PaperMention.created_at.desc()),
        prefetch_limit,
    )
    entity_ids = {
        item.canonical_entity_id for item in item_rows if item.canonical_entity_id
    }
    entity_ids.update(mention.canonical_entity_id for mention in mention_rows)

    # 阶段 2：从 entity 反向有界扩展一跳到 supporting paper。
    if max_hops >= 2 and entity_ids:
        related_mentions = fetch_bounded(
            select(PaperMention)
            .where(
                PaperMention.workspace_id == workspace_id,
                PaperMention.is_deleted.is_(False),
                PaperMention.canonical_entity_id.in_(entity_ids),
                PaperMention.status.not_in(REJECTED_STATUSES),
            )
            .order_by(PaperMention.confidence.desc(), PaperMention.created_at.desc()),
            prefetch_limit,
        )
        mention_by_id = {mention.id: mention for mention in mention_rows}
        mention_by_id.update({mention.id: mention for mention in related_mentions})
        mention_rows = list(mention_by_id.values())

        related_paper_ids = {mention.paper_id for mention in related_mentions}
        if related_paper_ids:
            related_papers = list(
                db.scalars(
                    select(Paper).where(
                        Paper.workspace_id == workspace_id,
                        Paper.is_deleted.is_(False),
                        Paper.id.in_(related_paper_ids),
                    )
                )
            )
            paper_by_id.update({paper.id: paper for paper in related_papers})
            valid_related_paper_ids = set(paper_by_id) - valid_seed_paper_ids
            if valid_related_paper_ids:
                related_items = fetch_bounded(
                    select(KnowledgeItem)
                    .where(
                        KnowledgeItem.workspace_id == workspace_id,
                        KnowledgeItem.is_deleted.is_(False),
                        KnowledgeItem.paper_id.in_(valid_related_paper_ids),
                        KnowledgeItem.canonical_entity_id.in_(entity_ids),
                        KnowledgeItem.type.in_(WORKSPACE_ENTITY_TYPES),
                        KnowledgeItem.status.not_in(REJECTED_STATUSES),
                    )
                    .order_by(KnowledgeItem.confidence.desc(), KnowledgeItem.created_at.desc()),
                    prefetch_limit,
                )
                item_by_id = {item.id: item for item in item_rows}
                item_by_id.update({item.id: item for item in related_items})
                item_rows = list(item_by_id.values())

    entity_rows = list(
        db.scalars(
            select(CanonicalEntity).where(
                CanonicalEntity.workspace_id == workspace_id,
                CanonicalEntity.is_deleted.is_(False),
                CanonicalEntity.id.in_(entity_ids),
                CanonicalEntity.type.in_(WORKSPACE_ENTITY_TYPES),
                CanonicalEntity.status.not_in(REJECTED_STATUSES),
            )
        )
    )
    entity_by_id = {entity.id: entity for entity in entity_rows}
    item_by_id = {
        item.id: item
        for item in item_rows
        if item.paper_id in paper_by_id
        and item.canonical_entity_id in entity_by_id
        and item.type in WORKSPACE_ENTITY_TYPES
        and item.status not in REJECTED_STATUSES
    }
    mention_rows = [
        mention
        for mention in mention_rows
        if mention.paper_id in paper_by_id
        and mention.canonical_entity_id in entity_by_id
        and mention.status not in REJECTED_STATUSES
    ]

    item_ids = set(item_by_id)
    evidence_rows = (
        fetch_bounded(
            select(EvidenceSpan)
            .where(
                EvidenceSpan.workspace_id == workspace_id,
                EvidenceSpan.is_deleted.is_(False),
                EvidenceSpan.knowledge_item_id.in_(item_ids),
            )
            .order_by(EvidenceSpan.confidence.desc(), EvidenceSpan.created_at.desc()),
            prefetch_limit,
        )
        if item_ids
        else []
    )
    evidence_by_item: dict[str, list[EvidenceSpan]] = defaultdict(list)
    evidence_by_id: dict[str, EvidenceSpan] = {}
    evidence_relevance_by_id: dict[str, float] = {}
    artifact_ids = {span.artifact_id for span in evidence_rows if span.artifact_id}
    artifact_by_id = {
        artifact.id: artifact
        for artifact in (
            list(
                db.scalars(
                    select(Artifact).where(
                        Artifact.workspace_id == workspace_id,
                        Artifact.is_deleted.is_(False),
                        Artifact.id.in_(artifact_ids),
                    )
                )
            )
            if artifact_ids
            else []
        )
    }
    for span in evidence_rows:
        item = item_by_id.get(span.knowledge_item_id)
        if (
            item is None
            or span.paper_id != item.paper_id
            or span.paper_id not in paper_by_id
            or (span.artifact_id is not None and span.artifact_id not in artifact_by_id)
        ):
            continue
        relevance = _evidence_relevance_score(span, item, query_text, dense_items)
        if query_text.strip() and relevance < GRAPH_RAG_MIN_EVIDENCE_RELEVANCE:
            continue
        evidence_by_item[item.id].append(span)
        evidence_by_id[span.id] = span
        evidence_relevance_by_id[span.id] = relevance

    for item_id, spans in evidence_by_item.items():
        evidence_by_item[item_id] = sorted(
            spans,
            key=lambda span: (
                -evidence_relevance_by_id.get(span.id, 0.0),
                -span.confidence,
                span.created_at,
            ),
        )

    # 只有当两个端点都是活跃的、按论文限定的 entity item 时，才纳入显式关系。
    # 这里刻意排除 claim 和 limitation，以保留它们的论文级上下文。
    relation_rows = (
        fetch_bounded(
            select(KnowledgeRelation)
            .where(
                KnowledgeRelation.workspace_id == workspace_id,
                KnowledgeRelation.is_deleted.is_(False),
                KnowledgeRelation.source_id.in_(item_ids),
                KnowledgeRelation.target_id.in_(item_ids),
                KnowledgeRelation.relation_type.in_(SAFE_RELATION_TYPES),
            )
            .order_by(
                KnowledgeRelation.confidence.desc(),
                KnowledgeRelation.created_at.desc(),
            ),
            prefetch_limit,
        )
        if item_ids
        else []
    )

    pair_items: dict[tuple[str, str], set[str]] = defaultdict(set)
    pair_statuses: dict[tuple[str, str], list[str]] = defaultdict(list)
    for item in item_by_id.values():
        key = (item.paper_id, item.canonical_entity_id)
        pair_items[key].add(item.id)
        pair_statuses[key].append(item.status)
    for mention in mention_rows:
        key = (mention.paper_id, mention.canonical_entity_id)
        if mention.knowledge_item_id in item_by_id:
            pair_items[key].add(mention.knowledge_item_id)
        pair_statuses[key].append(mention.status)

    seed_by_paper: dict[str, list[GraphRAGSeedRead]] = defaultdict(list)
    for seed in seeds:
        if seed.paper_id:
            seed_by_paper[seed.paper_id].append(seed)

    # 一篇论文可能贡献多个 dense chunk。为便于审计，seed 列表保留全部 chunk，
    # 但每条路径只使用最佳 chunk，避免重复的论文局部路径消耗有界节点预算。
    primary_seed_by_paper = {
        paper_id: min(
            paper_seeds,
            key=lambda seed: (-seed.score, seed.rank, seed.node_id),
        )
        for paper_id, paper_seeds in seed_by_paper.items()
    }
    dense_score_by_paper = {
        paper_id: max(seed.score for seed in paper_seeds)
        for paper_id, paper_seeds in seed_by_paper.items()
    }

    seed_rank_by_paper = {
        paper_id: min(seed.rank for seed in paper_seeds)
        for paper_id, paper_seeds in seed_by_paper.items()
    }

    def pair_sort_key(
        entry: tuple[tuple[str, str], set[str]],
    ) -> tuple[float, int, float, str, str]:
        (paper_id, entity_id), item_ids_for_pair = entry
        evidence_relevance = max(
            (
                evidence_relevance_by_id.get(span.id, 0.0)
                for item_id in item_ids_for_pair
                for span in evidence_by_item.get(item_id, [])
            ),
            default=0.0,
        )
        item_confidence = max(
            (item_by_id[item_id].confidence for item_id in item_ids_for_pair),
            default=0.0,
        )
        # 优先将有界节点预算分配给带证据的路径，
        # 然后保留 dense seed 顺序和确定性的平局处理规则。
        return (
            -evidence_relevance,
            seed_rank_by_paper.get(paper_id, 10**6),
            -item_confidence,
            paper_id,
            entity_id,
        )

    nodes: dict[str, GraphRAGNodeRead] = {}
    edges: dict[str, GraphRAGEdgeRead] = {}
    paths: list[GraphRAGPathRead] = []
    candidates: list[_PathCandidate] = []
    dropped_path_reasons: defaultdict[str, int] = defaultdict(int)
    evidence_reads: dict[str, GraphRAGEvidenceRead] = {}
    supporting_paper_ids: set[str] = set()
    supporting_evidence_ids: set[str] = set()
    truncated = query_truncated
    truncation_reason: str | None = "query_limit" if query_truncated else None

    # 即使找不到 graph 边，也保留诊断投影中的 dense seed。
    # 这样可以明确表示“找到 seed，但 graph 扩展为空”，而不是与空检索结果混淆。
    for seed in seeds:
        paper = paper_by_id.get(seed.paper_id or "")
        nodes[seed.node_id] = GraphRAGNodeRead(
            id=seed.node_id,
            kind=seed.node_kind,
            workspace_id=workspace_id,
            label=seed.chunk_id or (paper.title if paper else seed.node_id),
            paper_id=seed.paper_id,
            chunk_id=seed.chunk_id,
            type="chunk" if seed.chunk_id else "paper",
            review_status="candidate",
        )

    def can_add(candidate: _PathCandidate) -> bool:
        nonlocal truncated, truncation_reason
        new_node_count = len({node.id for node in candidate.nodes} - nodes.keys())
        new_edge_count = len({edge.id for edge in candidate.edges} - edges.keys())
        if len(nodes) + new_node_count > node_limit:
            truncated = True
            truncation_reason = "node_limit"
            dropped_path_reasons["node_limit"] += 1
            return False
        if len(edges) + new_edge_count > edge_limit:
            truncated = True
            truncation_reason = "edge_limit"
            dropped_path_reasons["edge_limit"] += 1
            return False
        return True

    def collect_candidate(
        path_id: str,
        path_nodes: list[GraphRAGNodeRead],
        path_edges: list[GraphRAGEdgeRead],
        path_evidence: list[GraphRAGEvidenceRead],
        *,
        paper_ids: Iterable[str],
        item_ids_for_path: Iterable[str],
        review_status: str,
        dense_score: float,
        confidence_score: float,
    ) -> None:
        # 路径只有通过本地端点校验后才接受。
        path_node_ids = {node.id for node in path_nodes}
        if not path_node_ids:
            return
        if any(
            edge.workspace_id != workspace_id
            or edge.source not in path_node_ids
            or edge.target not in path_node_ids
            for edge in path_edges
        ):
            return
        if evidence_required and not path_evidence:
            return
        candidates.append(
            _PathCandidate(
                path_id=path_id,
                nodes=path_nodes,
                edges=path_edges,
                evidence=path_evidence,
                paper_ids=sorted(set(paper_ids)),
                item_ids=sorted(set(item_ids_for_path)),
                review_status=review_status,
                evidence_relevance_score=max(
                    (item.query_relevance_score for item in path_evidence),
                    default=0.0,
                ),
                dense_score=dense_score,
                confidence_score=confidence_score,
            )
        )

    def emit_path(candidate: _PathCandidate) -> bool:
        if not can_add(candidate):
            return False
        path = GraphRAGPathRead(
            path_id=candidate.path_id,
            workspace_id=workspace_id,
            nodes=candidate.nodes,
            edges=candidate.edges,
            supporting_paper_ids=candidate.paper_ids,
            supporting_item_ids=candidate.item_ids,
            supporting_evidence_ids=sorted(
                {item.evidence_span_id for item in candidate.evidence}
            ),
            evidence=candidate.evidence,
            review_status=candidate.review_status,  # type: ignore[arg-type]
        )
        paths.append(path)
        nodes.update({node.id: node for node in candidate.nodes})
        edges.update({edge.id: edge for edge in candidate.edges})
        for item in candidate.evidence:
            evidence_reads[item.evidence_span_id] = item
            supporting_evidence_ids.add(item.evidence_span_id)
        supporting_paper_ids.update(path.supporting_paper_ids)
        return True

    def candidate_sort_key(
        candidate: _PathCandidate,
    ) -> tuple[float, float, float, int, int, str]:
        """优先保留有用证据，同时保持预算打包的确定性。"""

        return (
            -candidate.evidence_relevance_score,
            -candidate.dense_score,
            -candidate.confidence_score,
            candidate.node_count,
            candidate.edge_count,
            candidate.path_id,
        )

    # 构建 paper -> entity -> item -> evidence 路径。只有当 seed chunk 属于同一篇论文
    # 时才纳入；不能仅因为检索结果的 label 匹配就伪造 graph 节点。
    pair_index = 0
    for (paper_id, entity_id), item_ids_for_pair in sorted(
        pair_items.items(),
        key=pair_sort_key,
    ):
        paper = paper_by_id.get(paper_id)
        entity = entity_by_id.get(entity_id)
        if paper is None or entity is None:
            continue
        ordered_item_ids = sorted(
            item_ids_for_pair,
            key=lambda item_id: (
                -max(
                    (
                        evidence_relevance_by_id.get(span.id, 0.0)
                        for span in evidence_by_item.get(item_id, [])
                    ),
                    default=0.0,
                ),
                -item_by_id[item_id].confidence,
                item_by_id[item_id].created_at,
                item_id,
            ),
        )
        if evidence_required:
            ordered_item_ids = [
                item_id
                for item_id in ordered_item_ids
                if evidence_by_item.get(item_id)
            ]
        if not ordered_item_ids:
            continue
        selected_item_ids = ordered_item_ids[:2]
        selected_items = [item_by_id[item_id] for item_id in selected_item_ids]
        selected_evidence: list[GraphRAGEvidenceRead] = []
        for item in selected_items:
            # 每个 item 只保留一个最佳 span，使路径保持简洁。完整 EvidenceSpan 身份
            # 仍可用于 source navigation，同时避免相邻 span 耗尽节点预算。
            for span in evidence_by_item.get(item.id, [])[:1]:
                selected_evidence.append(
                    _evidence_read(
                        span,
                        item,
                        paper=paper,
                        artifact=artifact_by_id.get(span.artifact_id),
                        query_relevance_score=evidence_relevance_by_id.get(span.id, 1.0),
                    )
                )
        if evidence_required and not selected_evidence:
            # 没有问题相关 source span 的 graph relation 只是导航候选，不是有用的
            # shadow 路径。排除它也能为带证据路径保留节点预算。
            continue
        pair_index += 1
        review_status = _review_status(
            [*pair_statuses[(paper_id, entity_id)], *(item.status for item in selected_items)]
        )
        if not selected_evidence:
            # 即使 item 已确认，但没有当前 source span，在路径层面仍只是导航候选。
            review_status = "candidate"
        path_nodes: list[GraphRAGNodeRead] = []
        path_edges: list[GraphRAGEdgeRead] = []
        seed = primary_seed_by_paper.get(paper_id)
        if seed is not None:
            path_nodes.append(
                GraphRAGNodeRead(
                    id=seed.node_id,
                    kind=seed.node_kind,
                    workspace_id=workspace_id,
                    label=seed.chunk_id or paper.title,
                    paper_id=paper.id,
                    chunk_id=seed.chunk_id,
                    type="chunk",
                    review_status="candidate",
                )
            )
            path_edges.append(
                GraphRAGEdgeRead(
                    id=f"edge:seed:{seed.node_id}:{_node_id('paper', paper.id)}",
                    type="seed_from_chunk",
                    source=seed.node_id,
                    target=_node_id("paper", paper.id),
                    workspace_id=workspace_id,
                    paper_id=paper.id,
                    review_status="candidate",
                )
            )
        path_nodes.extend(
            [
                GraphRAGNodeRead(
                    id=_node_id("paper", paper.id),
                    kind="paper",
                    workspace_id=workspace_id,
                    label=paper.title,
                    paper_id=paper.id,
                    type="paper",
                    status=paper.parse_status,
                    review_status="candidate",
                ),
                GraphRAGNodeRead(
                    id=_node_id("entity", entity.id),
                    kind="canonical_entity",
                    workspace_id=workspace_id,
                    label=entity.canonical_name,
                    canonical_entity_id=entity.id,
                    type=entity.type,
                    status=entity.status,
                    review_status=_review_status([entity.status]),  # type: ignore[arg-type]
                ),
            ]
        )
        path_edges.append(
            GraphRAGEdgeRead(
                id=f"edge:paper_entity:{paper.id}:{entity.id}",
                type="paper_mentions_entity",
                source=_node_id("paper", paper.id),
                target=_node_id("entity", entity.id),
                workspace_id=workspace_id,
                paper_id=paper.id,
                supporting_item_ids=selected_item_ids,
                supporting_evidence_ids=[item.evidence_span_id for item in selected_evidence],
                review_status=review_status,  # type: ignore[arg-type]
            )
        )
        # Item 和 EvidenceSpan 是 provenance 载荷，而不是 shadow graph 的拓扑节点。
        # 它们的身份和审阅状态保留在上面的路径/边字段中，避免重复证据消耗有界拓扑预算。
        collect_candidate(
            f"path:{request_id}:{pair_index}",
            path_nodes,
            path_edges,
            selected_evidence,
            paper_ids=[paper.id],
            item_ids_for_path=selected_item_ids,
            review_status=review_status,
            dense_score=seed.score if seed is not None else 0.0,
            confidence_score=max(
                [item.confidence for item in selected_items]
                + [item.confidence for item in selected_evidence],
                default=0.0,
            ),
        )

    # 只添加安全的 entity-to-entity relation 路径，并保留原始 item/evidence ID。
    # Claim 和 limitation 永远不会进入此分支。
    relation_index = pair_index
    seen_relation_signatures: set[tuple[str, str, str, tuple[str, ...]]] = set()
    for relation in relation_rows:
        source_item = item_by_id.get(relation.source_id)
        target_item = item_by_id.get(relation.target_id)
        if source_item is None or target_item is None:
            continue
        source_entity = entity_by_id.get(source_item.canonical_entity_id)
        target_entity = entity_by_id.get(target_item.canonical_entity_id)
        if source_entity is None or target_entity is None or source_entity.id == target_entity.id:
            continue
        relation_evidence: list[GraphRAGEvidenceRead] = []
        for item in (source_item, target_item):
            paper = paper_by_id.get(item.paper_id)
            if paper is None:
                continue
            relation_evidence.extend(
                _evidence_read(
                    span,
                    item,
                    paper=paper,
                    artifact=artifact_by_id.get(span.artifact_id),
                    query_relevance_score=evidence_relevance_by_id.get(span.id, 1.0),
                )
                for span in evidence_by_item.get(item.id, [])[:1]
            )
        if evidence_required and (
            not relation_evidence
            or not evidence_by_item.get(source_item.id)
            or not evidence_by_item.get(target_item.id)
        ):
            continue
        relation_signature = (
            relation.relation_type,
            source_entity.id,
            target_entity.id,
            tuple(sorted(ev.evidence_span_id for ev in relation_evidence)),
        )
        if relation_signature in seen_relation_signatures:
            continue
        seen_relation_signatures.add(relation_signature)
        relation_index += 1
        relation_status = _review_status(
            [(relation.payload or {}).get("review_status"), source_item.status, target_item.status]
        )
        if not relation_evidence:
            relation_status = "candidate"
        path_nodes = [
            GraphRAGNodeRead(
                id=_node_id("entity", source_entity.id),
                kind="canonical_entity",
                workspace_id=workspace_id,
                label=source_entity.canonical_name,
                canonical_entity_id=source_entity.id,
                type=source_entity.type,
                status=source_entity.status,
                review_status=_review_status([source_entity.status]),  # type: ignore[arg-type]
            ),
            GraphRAGNodeRead(
                id=_node_id("entity", target_entity.id),
                kind="canonical_entity",
                workspace_id=workspace_id,
                label=target_entity.canonical_name,
                canonical_entity_id=target_entity.id,
                type=target_entity.type,
                status=target_entity.status,
                review_status=_review_status([target_entity.status]),  # type: ignore[arg-type]
            ),
        ]
        path_edges = [
            GraphRAGEdgeRead(
                id=f"edge:relation:{relation.id}",
                type=relation.relation_type,
                source=_node_id("entity", source_entity.id),
                target=_node_id("entity", target_entity.id),
                workspace_id=workspace_id,
                paper_id=(
                    source_item.paper_id
                    if source_item.paper_id == target_item.paper_id
                    else None
                ),
                supporting_item_ids=[source_item.id, target_item.id],
                supporting_evidence_ids=[ev.evidence_span_id for ev in relation_evidence],
                review_status=relation_status,  # type: ignore[arg-type]
            )
        ]
        collect_candidate(
            f"path:{request_id}:relation:{relation_index}",
            path_nodes,
            path_edges,
            relation_evidence,
            paper_ids=[source_item.paper_id, target_item.paper_id],
            item_ids_for_path=[source_item.id, target_item.id],
            review_status=relation_status,
            dense_score=max(
                dense_score_by_paper.get(source_item.paper_id, 0.0),
                dense_score_by_paper.get(target_item.paper_id, 0.0),
            ),
            confidence_score=max(
                [relation.confidence, source_item.confidence, target_item.confidence]
                + [item.confidence for item in relation_evidence],
                default=0.0,
            ),
        )

    # 对所有符合条件的候选进行全局打包。无法装入剩余预算的候选不能阻止后续更小、
    # 更相关的候选输出。
    for candidate in sorted(candidates, key=candidate_sort_key):
        emit_path(candidate)

    # 防御性最终校验：即使未来分支增加新的路径构建器，损坏的边也绝不能离开此投影。
    valid_node_ids = set(nodes)
    edges = {
        edge_id: edge
        for edge_id, edge in edges.items()
        if edge.source in valid_node_ids and edge.target in valid_node_ids
    }
    paths = [
        path.model_copy(
            update={
                "edges": [
                    edge
                    for edge in path.edges
                    if edge.source in {node.id for node in path.nodes}
                    and edge.target in {node.id for node in path.nodes}
                ]
            }
        )
        for path in paths
    ]
    return BoundedGraphProjection(
        projection_version=GRAPH_RAG_PROJECTION_VERSION,
        seeds=seeds,
        paths=paths,
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        evidence=list(evidence_reads.values()),
        supporting_paper_ids=sorted(supporting_paper_ids),
        supporting_evidence_ids=sorted(supporting_evidence_ids),
        truncated=truncated,
        truncation_reason=truncation_reason,
        candidate_path_count=len(candidates),
        emitted_path_count=len(paths),
        dropped_path_count=sum(dropped_path_reasons.values()),
        dropped_path_reasons=dict(dropped_path_reasons),
    )
