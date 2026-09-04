"""诊断 similar_work / counter_evidence Gold 论文在哪一步丢失。

只读。给定源论文和 Gold 论文集合，逐步运行 ``find_similar_work`` 的召回阶段
（以及反证流水线），报告每篇 Gold 论文是否通过：
  1. 向量召回（top_k*4 / top_k*3）
  2. 低价值章节过滤
  3. 单论文分块上限（仅 similar）
  4. 重排 -> top_k

用法（从 backend/ 目录运行）：
    .venv/Scripts/python.exe scripts/diag_retrieval_loss.py --source <paper_id> --golds g1,g2 --purpose similar
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.db.models  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.domains.paper.models import Paper  # noqa: E402
from app.gateway.embedding import get_embedding_gateway  # noqa: E402
from app.domains.retrieval import milvus_client  # noqa: E402
from app.domains.retrieval.service import (  # noqa: E402
    SIMILAR_WORK_MAX_CHUNKS_PER_PAPER,
    _is_low_value_section,
    _spread_sample_indices,
    _load_chunks_jsonl,
)


def paper_title(db, pid: str | None) -> str:
    if not pid:
        return "?"
    p = db.get(Paper, pid)
    return p.title[:55] if p else "?"


def diagnose_similar(db, workspace_id: str, source_id: str, golds: list[str]) -> None:
    gateway = get_embedding_gateway()
    chunks = _load_chunks_jsonl(db, workspace_id, source_id)
    idx = _spread_sample_indices(len(chunks), max_samples=5)
    query_texts = [chunks[i].text for i in idx]
    vecs = gateway.embed_texts(query_texts).embeddings

    seen: set[str] = set()
    all_hits = []
    for v in vecs:
        hits = milvus_client.search(v, workspace_id, top_k=40, exclude_paper_ids={source_id})
        for h in hits:
            cid = h.get("chunk_id", "")
            if cid in seen:
                continue
            seen.add(cid)
            all_hits.append(h)
    print(f"source={paper_title(db, source_id)}  recall_hits={len(all_hits)}")
    print(f"  rerank_query = sample-chunk#0 ({query_texts[0][:40]!r}...)")

    for g in golds:
        gpaper = db.get(Paper, g)
        print(f"\n  GOLD {paper_title(db, g)}")
        raw = [h for h in all_hits if h.get("paper_id") == g]
        print(f"    in recall(top40x5): {len(raw)} chunks; scores={[round(h.get('score',0),4) for h in raw]}")
        non_low = [h for h in raw if not _is_low_value_section(h.get("section"))]
        print(f"    after low-value filter: {len(non_low)} chunks; sections={[h.get('section') for h in raw]}")
        if non_low:
            capped = sorted(non_low, key=lambda h: h.get("score", 0), reverse=True)[:SIMILAR_WORK_MAX_CHUNKS_PER_PAPER]
            print(f"    per-paper cap({SIMILAR_WORK_MAX_CHUNKS_PER_PAPER}): {len(capped)} chunks kept")
            print(f"    top candidate chunk preview: {capped[0].get('text','')[:90]!r}")

# 重排：以 query_texts[0] 为依据重排后，是否有 Gold 论文进入 Top-10？
    filtered = [h for h in all_hits if not _is_low_value_section(h.get("section"))] or all_hits
    by_paper: dict[str, list] = {}
    for h in filtered:
        by_paper.setdefault(h.get("paper_id") or "", []).append(h)
    cands = []
    for pid, hs in by_paper.items():
        hs.sort(key=lambda h: h.get("score", 0), reverse=True)
        cands.extend(hs[:SIMILAR_WORK_MAX_CHUNKS_PER_PAPER])
    from app.domains.retrieval.service import _rerank_hits

# (a) 原始 Milvus 分数 Top-10（不重排）
    raw = sorted(cands, key=lambda h: h.get("score", 0), reverse=True)[:10]
    raw_pids = [h.get("paper_id") for h in raw]
    print("\n  raw-score top10:")
    for h in raw[:10]:
        print(f"    {paper_title(db, h.get('paper_id'))[:38]:38} score={h.get('score'):.4f}")
    for g in golds:
        print(f"    GOLD {paper_title(db, g)[:30]} -> {'SURVIVES' if g in raw_pids else 'LOST at raw'}")

# (b) 仅使用 sample-chunk#0 重排（当前行为）
    items = _rerank_hits(query_texts[0][:500], cands, 10)
    top_pids = [getattr(i, "paper_id", None) for i in items]
    print("\n  rerank(chunk#0) top10:")
    for i in items:
        print(f"    {paper_title(db, getattr(i, 'paper_id', None))[:38]:38} score={getattr(i, 'score', 0):.4f}")
    for g in golds:
        print(f"    GOLD {paper_title(db, g)[:30]} -> {'SURVIVES' if g in top_pids else 'LOST at rerank'}")

# (c) 拼接所有示例分块后重排（代表性查询）
    multi_query = ("\n".join(query_texts))[:1000]
    items2 = _rerank_hits(multi_query, cands, 10)
    top2 = [getattr(i, "paper_id", None) for i in items2]
    print("\n  rerank(all-5-chunks) top10:")
    for i in items2:
        print(f"    {paper_title(db, getattr(i, 'paper_id', None))[:38]:38} score={getattr(i, 'score', 0):.4f}")
    for g in golds:
        print(f"    GOLD {paper_title(db, g)[:30]} -> {'SURVIVES' if g in top2 else 'LOST at rerank'}")

# (d) rerank(chunk#0) + 论文去重：每篇论文一个结果，因此 10 个槽位对应 10 篇不同论文
    seen_papers: set[str] = set()
    dedup_items: list = []
    for i in items:  # items is sorted by rerank score desc
        pid = getattr(i, "paper_id", None)
        if pid in seen_papers:
            continue
        seen_papers.add(pid)
        dedup_items.append(i)
    dedup_top10 = dedup_items[:10]
    topd = [getattr(i, "paper_id", None) for i in dedup_top10]
    print("\n  rerank(chunk#0) + paper-dedup top10:")
    for i in dedup_top10:
        print(f"    {paper_title(db, getattr(i, 'paper_id', None))[:38]:38} score={getattr(i, 'score', 0):.4f}")
    for g in golds:
        print(f"    GOLD {paper_title(db, g)[:30]} -> {'SURVIVES' if g in topd else 'LOST'}")

# (e) 重排所有候选 -> 按论文取最高分 -> Top-10 论文
    all_items = _rerank_hits(query_texts[0][:500], cands, len(cands))
    by_pid: dict[str, Any] = {}
    for i in all_items:
        pid = getattr(i, "paper_id", None)
        if pid is None:
            continue
        if pid not in by_pid or getattr(i, "score", 0) > by_pid[pid].get("_score", 0):
            by_pid[pid] = {"item": i, "_score": getattr(i, "score", 0)}
    rank_papers = sorted(by_pid.items(), key=lambda kv: kv[1]["_score"], reverse=True)[:10]
    tope = [pid for pid, _ in rank_papers]
    print("\n  rerank-all -> per-paper max -> top10 papers:")
    for pid, rec in rank_papers:
        print(f"    {paper_title(db, pid)[:38]:38} score={rec['_score']:.4f}")
    for g in golds:
        print(f"    GOLD {paper_title(db, g)[:30]} -> {'SURVIVES' if g in tope else 'LOST'}")

# (f) HYBRID：0.5*归一化原始分数 + 0.5*重排分数，然后按论文取最高分 -> Top-10
# 原始分数 -> 项分数映射，按分块 ID 索引
    raw_by_chunk = {h.get("chunk_id"): h.get("score", 0) for h in cands}
    if raw_by_chunk:
        rmin, rmax = min(raw_by_chunk.values()), max(raw_by_chunk.values())
    all_items2 = _rerank_hits(query_texts[0][:500], cands, len(cands))
    s_min = min(getattr(i, "score", 0) for i in all_items2) if all_items2 else 0
    s_max = max(getattr(i, "score", 0) for i in all_items2) if all_items2 else 1
    span_r = (rmax - rmin) or 1.0
    span_s = (s_max - s_min) or 1.0
    best_paper: dict[str, tuple[float, str]] = {}
    for i in all_items2:
        pid = getattr(i, "paper_id", None)
        if pid is None:
            continue
        raw = raw_by_chunk.get(getattr(i, "chunk_id", ""), 0)
        nr = (raw - rmin) / span_r
        ns = (getattr(i, "score", 0) - s_min) / span_s
        hybrid = 0.5 * nr + 0.5 * ns
        if pid not in best_paper or hybrid > best_paper[pid][0]:
            best_paper[pid] = (hybrid, getattr(i, "score", 0))
    rankh = sorted(best_paper.items(), key=lambda kv: kv[1][0], reverse=True)[:10]
    toph = [pid for pid, _ in rankh]
    print("\n  HYBRID (0.5 raw + 0.5 rerank) -> per-paper max -> top10:")
    for pid, (hy, sc) in rankh:
        mark = " *" if pid in golds else ""
        print(f"    {paper_title(db, pid)[:38]:38} hybrid={hy:.3f}{mark}")
    for g in golds:
        print(f"    GOLD {paper_title(db, g)[:30]} -> {'SURVIVES' if g in toph else 'LOST'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--golds", required=True)
    parser.add_argument("--workspace-id", default="123100ea-e75b-4110-9048-1f5b92668c32")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        diagnose_similar(db, args.workspace_id, args.source, [g.strip() for g in args.golds.split(",")])
    finally:
        db.close()


if __name__ == "__main__":
    main()
