"""Retrieval Gate 评测运行器。

加载 GoldSet（见 ``gold_set.py``），在 live workspace 上运行三个 retrieval 函数，并生成
回答 Stage-2 Gate 问题的 JSON 报告（docs/phase3_smoke_validation_and_next_plan.md §6 V2）：

    Semantic Search    Recall@10 ≥ 0.80
    Similar Work       Recall@10 ≥ 0.80
    Counter Evidence   Recall@10 ≥ 0.70
    workspace 泄露     = 0
    每条结果都可回链到 Paper + artifact

用法：

# 仓库根目录（脚本会自行将 backend + evaluation 加入 sys.path）
    python evaluation/retrieval/run_eval.py \
        --workspace-id <uuid> \
        --gold evaluation/retrieval/gold/demo_sig_ood_v1.json \
        [--minimal] [--top-k 10] [--output evaluation/retrieval/reports/demo_v1.json]

``--minimal`` 跳过 LLM judge（低成本 smoke；真实 Gate 必须运行 judge，才能让
counter-evidence role 有意义）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

# --- sys.path：允许从仓库根目录或 backend/ 直接运行，无需 `-m` ---
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
for p in (str(BACKEND_DIR), str(REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from sqlalchemy import select  # noqa: E402

import app.db.models  # noqa: E402,F401  (register all models)
from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.domains.paper.models import Paper  # noqa: E402
from app.domains.retrieval.service import (  # noqa: E402
    find_counter_evidence,
    find_similar_work,
    semantic_search,
)
from evaluation.retrieval.gold_set import (  # noqa: E402
    CounterEvidenceQuery,
    GoldSet,
    SemanticSearchQuery,
    SimilarWorkQuery,
)
from evaluation.retrieval.metrics import (  # noqa: E402
    gate_report,
    mrr_at_k,
    ndcg_at_k,
    paper_diversity,
    recall_at_k,
    workspace_leakage,
)

# Stage-2 Gate 阈值（docs/phase3_smoke_validation_and_next_plan.md §6 V2）。
GATE_THRESHOLDS = {
    "semantic_search": 0.80,
    "similar_work": 0.80,
    "counter_evidence": 0.70,
}


# ------------------------------------------------------------ 论文引用
def resolve_paper_ref(db, workspace_id: str, paper_ref: str) -> Paper | None:
    """将可移植论文引用解析为本地 Paper 行。

优先级：
1. 精确的本地 UUID 匹配（``Paper.id``）
2. Semantic Scholar external ID 匹配（``Paper.external_paper_id``）
3. 标题匹配（不区分大小写的精确匹配，然后是前缀匹配）
所有匹配都限定在 workspace 内；存在于其他 workspace 的 ref 按未解析处理，以避免跨作用域泄露。
    """
    ref = paper_ref.strip()
    if not ref:
        return None

    base = select(Paper).where(
        Paper.workspace_id == workspace_id,
        Paper.is_deleted.is_(False),
    )

    # 1. UUID：本地 ID
    paper = db.execute(base.where(Paper.id == ref)).scalars().first()
    if paper is not None:
        return paper

    # 2. external ID：外部 ID
    paper = db.execute(
        base.where(Paper.external_paper_id == ref)
    ).scalars().first()
    if paper is not None:
        return paper

# 3. 标题（不区分大小写精确匹配，再尝试前缀）
    lowered = ref.lower()
    for candidate in db.execute(base).scalars().all():
        title = (candidate.title or "").strip()
        if title.lower() == lowered:
            return candidate
    for candidate in db.execute(base).scalars().all():
        title = (candidate.title or "").strip()
        if title.lower().startswith(lowered):
            return candidate
    return None


def resolve_many(db, workspace_id: str, refs: list[str]) -> dict[str, str | None]:
    """将每个 paper_ref 映射到本地 UUID（无法解析时为 ``None``）。"""
    resolved: dict[str, str | None] = {}
    for ref in refs:
        paper = resolve_paper_ref(db, workspace_id, ref)
        resolved[ref] = paper.id if paper is not None else None
    return resolved


# ------------------------------------------------------------ 单查询
def _paper_ids(items: list[Any]) -> list[str]:
    """从 RetrievalResultItems 中提取去重且有序的 paper_ids。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        pid = getattr(item, "paper_id", None)
        if pid and pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def _paper_workspace_ids(db, items: list[Any], target_workspace_id: str) -> list[str]:
    """通过数据库解析每个检索条目的 Workspace id。

    ``RetrievalResultItem`` 携带 ``paper_id``，但不携带 ``workspace_id``（workspace 限定在
    Milvus filter 内完成）。为计算真实泄露情况，我们逐个查找 paper；未知或 None 的
    paper_id 按范围内处理（按构造方式它们不可能跨过 workspace 边界）。
    """
    paper_ids = [getattr(item, "paper_id", None) for item in items]
    present = [pid for pid in paper_ids if pid]
    wmap: dict[str, str] = {}
    if present:
        for row in db.query(Paper).filter(Paper.id.in_(present)).all():
            wmap[row.id] = row.workspace_id
    return [wmap.get(pid, target_workspace_id) if pid else target_workspace_id for pid in paper_ids]


def run_semantic_search(db, workspace_id: str, q: SemanticSearchQuery, top_k: int, minimal: bool):
    target = resolve_paper_ref(db, workspace_id, q.target_paper_ref)
    if target is None:
        return {"query_id": q.query_id, "error": f"unresolved target_paper_ref: {q.target_paper_ref}"}

    resp = semantic_search(workspace_id, q.query, top_k=top_k, use_reranker=not minimal)
    pids = _paper_ids(resp.items)
    return {
        "query_id": q.query_id,
        "query": q.query,
        "target_paper_id": target.id,
        "retrieved_paper_ids": pids,
        "status": resp.status,
        "count": len(pids),
        f"recall@{top_k}": recall_at_k({target.id}, pids, top_k),
        f"mrr@{top_k}": mrr_at_k({target.id}, pids, top_k),
        "leakage": workspace_leakage(_paper_workspace_ids(db, resp.items, workspace_id), workspace_id),
    }


def run_similar_work(db, workspace_id: str, q: SimilarWorkQuery, top_k: int, minimal: bool):
    source = resolve_paper_ref(db, workspace_id, q.source_paper_ref)
    if source is None:
        return {"query_id": q.query_id, "error": f"unresolved source_paper_ref: {q.source_paper_ref}"}
    resolved_gold = resolve_many(db, workspace_id, q.relevant_paper_refs)
    gold_ids = {pid for pid in resolved_gold.values() if pid}
    if not gold_ids:
        return {"query_id": q.query_id, "error": "no gold relevant papers resolved"}

    resp = find_similar_work(
        workspace_id,
        source.id,
        top_k=top_k,
        use_reranker=not minimal,
        exclude_paper_ids={source.id},
    )
    pids = _paper_ids(resp.items)
    return {
        "query_id": q.query_id,
        "source_paper_id": source.id,
        "gold_paper_ids": sorted(gold_ids),
        "retrieved_paper_ids": pids,
        "status": resp.status,
        "count": len(pids),
        f"recall@{top_k}": recall_at_k(gold_ids, pids, top_k),
        f"mrr@{top_k}": mrr_at_k(gold_ids, pids, top_k),
        "diversity": paper_diversity(pids, top_k),
        "leakage": workspace_leakage(_paper_workspace_ids(db, resp.items, workspace_id), workspace_id),
        "source_leaked": source.id in pids,
    }


def run_counter_evidence(db, workspace_id: str, q: CounterEvidenceQuery, top_k: int, minimal: bool):
    source = resolve_paper_ref(db, workspace_id, q.source_paper_ref)
    if source is None:
        return {"query_id": q.query_id, "error": f"unresolved source_paper_ref: {q.source_paper_ref}"}
    resolved_gold = resolve_many(db, workspace_id, [r.paper_ref for r in q.gold_roles])
    gold_ids = {pid for pid in resolved_gold.values() if pid}
    if not gold_ids:
        return {"query_id": q.query_id, "error": "no gold counter-evidence papers resolved"}

    resp = find_counter_evidence(
        workspace_id,
        q.claim_text,
        top_k=top_k,
        use_reranker=True,
        use_judge=not minimal,
        exclude_paper_ids={source.id},
    )
    pids = _paper_ids(resp.items)
# 角色正确召回仅用于诊断（Stage-2 阈值针对论文召回）。
    role_paper_ids = {pid for ref, pid in resolved_gold.items() if pid}
    roles_by_paper: dict[str, str] = {
        resolved_gold[r.paper_ref]: r.role
        for r in q.gold_roles
        if resolved_gold.get(r.paper_ref)
    }
    role_correct = sum(
        1 for pid in pids[:top_k] if roles_by_paper.get(pid) == "contradicts"
    ) + 0.5 * sum(
        1 for pid in pids[:top_k] if roles_by_paper.get(pid) == "qualifies"
    )
    role_recall = role_correct / len(gold_ids) if gold_ids else 0.0

    return {
        "query_id": q.query_id,
        "source_paper_id": source.id,
        "gold_paper_ids": sorted(gold_ids),
        "retrieved_paper_ids": pids,
        "status": resp.status,
        "count": len(pids),
        f"recall@{top_k}": recall_at_k(gold_ids, pids, top_k),
        f"mrr@{top_k}": mrr_at_k(gold_ids, pids, top_k),
        "diversity": paper_diversity(pids, top_k),
        "leakage": workspace_leakage(_paper_workspace_ids(db, resp.items, workspace_id), workspace_id),
        "source_leaked": source.id in pids,
        "role_recall_diagnostic": round(role_recall, 4),
    }


# ------------------------------------------------------------ 聚合
def _aggregate(entries: list[dict], keys: tuple[str, ...]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for key in keys:
        vals = [e[key] for e in entries if key in e and isinstance(e[key], (int, float))]
        out[key] = round(sum(vals) / len(vals), 4) if vals else None
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=False, help="Local workspace UUID (overrides gold set hint).")
    parser.add_argument("--gold", type=str, required=True, help="Path to the gold-set JSON file.")
    parser.add_argument("--minimal", action="store_true", help="Skip LLM judge (cheap smoke; real Gate must run judge).")
    parser.add_argument("--top-k", type=int, default=10, help="Top-K for the Gate (default 10).")
    parser.add_argument("--output", type=str, default=None, help="Report output path (default: reports/<case_id>_<ts>.json).")
    args = parser.parse_args()

    gold_path = Path(args.gold)
    if not gold_path.exists():
        print(f"Gold set not found: {gold_path}")
        return 1
    with gold_path.open("r", encoding="utf-8") as f:
        gold = GoldSet.model_validate(json.load(f))

    workspace_id = args.workspace_id or gold.workspace_hint
    if not workspace_id:
        print("No workspace_id. Pass --workspace-id or set workspace_hint in the gold set.")
        return 1

    db = SessionLocal()
    try:
        return _run(db, gold, workspace_id, args)
    finally:
        db.close()


def _run(db, gold: GoldSet, workspace_id: str, args: argparse.Namespace) -> int:
    top_k = args.top_k
    minimal = args.minimal
    print(f"=== Retrieval Gate Evaluation ===")
    print(f"Case: {gold.case_id} | Corpus: {gold.corpus_version}")
    print(f"Workspace: {workspace_id} | Top-K: {top_k} | Minimal: {minimal}")
    print(f"Freeze: {gold.freeze.model_dump()}")

# 一次性解析论文清单（按 workspace 限定），使无法解析的引用明确暴露。
    all_refs = {
        q.target_paper_ref for q in gold.semantic_search
    } | {
        q.source_paper_ref for q in gold.similar_work
    } | {r.paper_ref for q in gold.counter_evidence for r in q.gold_roles} | {
        q.source_paper_ref for q in gold.counter_evidence
    } | {r for q in gold.similar_work for r in q.relevant_paper_refs}
    resolved = resolve_many(db, workspace_id, sorted(all_refs))
    unresolved = [ref for ref, pid in resolved.items() if pid is None]
    if unresolved:
        print("\n[WARN] unresolved paper refs (will be skipped):")
        for ref in unresolved:
            print(f"  - {ref}")
        if args.workspace_id is None:
            print("  (Tip: this often means the workspace_hint is wrong, or the corpus isn't indexed there.)")

    ss_entries = [run_semantic_search(db, workspace_id, q, top_k, minimal) for q in gold.semantic_search]
    sw_entries = [run_similar_work(db, workspace_id, q, top_k, minimal) for q in gold.similar_work]
    ce_entries = [run_counter_evidence(db, workspace_id, q, top_k, minimal) for q in gold.counter_evidence]

    report: dict[str, Any] = {
        "schema_version": "1.0.0",
        "case_id": gold.case_id,
        "corpus_version": gold.corpus_version,
        "workspace_id": workspace_id,
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "freeze": gold.freeze.model_dump(),
        "top_k": top_k,
        "minimal": minimal,
        "unresolved_paper_refs": unresolved,
        "gate": {},
        "semantic_search": ss_entries,
        "similar_work": sw_entries,
        "counter_evidence": ce_entries,
    }

    recall_key = f"recall@{top_k}"
    mrr_key = f"mrr@{top_k}"

    def valid(entries: list[dict]) -> list[dict]:
        return [e for e in entries if recall_key in e and isinstance(e[recall_key], (int, float))]

    for name, entries in (("semantic_search", ss_entries), ("similar_work", sw_entries), ("counter_evidence", ce_entries)):
        valid_entries = valid(entries)
        agg = _aggregate(valid_entries, (recall_key, mrr_key, "diversity", "leakage"))
        threshold = GATE_THRESHOLDS[name]
        report["gate"][name] = gate_report(
            recall=agg[recall_key] or 0.0,
            threshold=threshold,
            k=top_k,
            mrr=agg[mrr_key],
            ndcg=None,
            diversity=agg["diversity"],
            leakage=agg["leakage"],
        )
# 添加诊断计数
        report["gate"][name]["queries"] = len(valid_entries)
        report["gate"][name]["resolved_queries"] = len(valid_entries)
        report["gate"][name]["unresolved_queries"] = len(entries) - len(valid_entries)

    overall_passed = all(report["gate"][name]["passed"] for name in report["gate"])
    report["gate_passed"] = overall_passed

# 打印
    print("\n=== Gate verdict ===")
    for name, block in report["gate"].items():
        status = "PASS" if block["passed"] else "FAIL"
        print(f"  [{status}] {name}: recall@{top_k}={block[recall_key]} "
              f"(threshold {block['recall_threshold']}) leakage={block['workspace_leakage']}")
    print(f"  overall: {'PASS' if overall_passed else 'FAIL'}")

# 保存
    output_path = Path(args.output) if args.output else (
        Path(__file__).parent / "reports" / f"{gold.case_id}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport saved to: {output_path}")
    return 0 if overall_passed else 2


if __name__ == "__main__":
    sys.exit(main())
