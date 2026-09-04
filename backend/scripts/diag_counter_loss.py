"""诊断反证丢失：Gold 论文在哪一步被丢弃？

流水线：recall(top_k*3) -> rerank(top_k chunks) -> judge -> diversify。
展示每篇 Gold 论文在各阶段的存活情况。
"""

from __future__ import annotations
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.db.models
from app.db.session import SessionLocal
from app.domains.paper.models import Paper
from app.domains.retrieval import milvus_client
from app.domains.retrieval.service import _rerank_hits, _hit_to_result_item, _judge_items
from app.gateway.embedding import get_embedding_gateway

WS = "123100ea-e75b-4110-9048-1f5b92668c32"


def main(claim_text: str, source_id: str, gold_ids: list[str]) -> None:
    db = SessionLocal()
    try:
        gateway = get_embedding_gateway()
        vec = gateway.embed_one(claim_text)
        hits = milvus_client.search(vec, WS, top_k=30, exclude_paper_ids={source_id})
        print(f"recall hits: {len(hits)}")
        for g in gold_ids:
            gp = db.get(Paper, g)
            raw = [h for h in hits if h.get("paper_id") == g]
            raw_sorted = sorted(raw, key=lambda h: h.get("score", 0), reverse=True)
# 该论文最佳分块在所有召回命中中的原始总体排名
            all_sorted = sorted(hits, key=lambda h: h.get("score", 0), reverse=True)
            best_rank = next((i for i, h in enumerate(all_sorted) if h.get("paper_id") == g), None)
            print(f"\n== GOLD {gp.title[:55]}")
            print(f"   recall: {len(raw)} chunks; best raw rank={best_rank}; "
                  f"scores={[round(h.get('score',0),3) for h in raw_sorted[:3]]}")
            for h in raw_sorted[:2]:
                print(f"     preview: {h.get('text','')[:110]!r}")

# Stage 2：重排 Top-10 分块
        reranked = _rerank_hits(claim_text, hits, 10)
        print(f"\nrerank top10 chunks: {len(reranked)}")
        for i in reranked:
            print(f"   {db.get(Paper, i.paper_id).title[:38]:38} {i.score:.4f}")
        for g in gold_ids:
            in_top10 = any(getattr(i, "paper_id", None) == g for i in reranked)
            print(f"   GOLD {db.get(Paper, g).title[:30]} -> {'in rerank top10' if in_top10 else 'LOST at rerank'}")

# Stage 3：判断重排后的项
        judged = _judge_items(claim_text, reranked)
        print(f"\njudge on {len(judged)} items:")
        for i in judged:
            tag = " *" if getattr(i, "paper_id", None) in gold_ids else ""
            print(f"   {db.get(Paper, i.paper_id).title[:34]:34} judgement={i.judgement} conf={i.judgement_confidence:.2f}{tag}")

# 方案：重排所有召回候选 -> 按论文取最高分 -> Top-10 论文
        all_ranked = _rerank_hits(claim_text, hits, len(hits))
        by_pid = {}
        for i in all_ranked:
            pid = getattr(i, "paper_id", None)
            if pid is None:
                continue
            if pid not in by_pid or getattr(i, "score", 0) > by_pid[pid][1]:
                by_pid[pid] = (i, getattr(i, "score", 0))
        ranked = sorted(by_pid.items(), key=lambda kv: kv[1][1], reverse=True)[:10]
        print(f"\nproposal: rerank-all -> per-paper max -> top10 ({len(by_pid)} distinct papers):")
        for pid, (i, sc) in ranked:
            tag = " *" if pid in gold_ids else ""
            print(f"   {db.get(Paper, pid).title[:34]:34} {sc:.4f}{tag}")
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3].split(","))
