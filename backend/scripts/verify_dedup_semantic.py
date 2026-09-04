"""在真实抽取项上验证 P1 语义去重（只读）。

加载工作区中的 claim/limitation KnowledgeItem，使用真实 BGE-m3 网关在论文并集上运行
``dedup_semantic``（跨论文保护天然生效），报告阈值 0.90 下将要合并的内容，
并报告近似命中对（>= 0.80）供调参。不会写入任何内容。

用法（从 backend/ 目录运行）：

    .venv/Scripts/python.exe scripts/verify_dedup_semantic.py --workspace-id 533c89cd-...

需要配置 SILICONFLOW_API_KEY（每篇论文执行一个 embedding 批次）。
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
from app.domains.knowledge.models import KnowledgeItem  # noqa: E402
from app.gateway.embedding import get_embedding_gateway  # noqa: E402
from app.workers.tasks.extraction.dedup import (  # noqa: E402
    SEMANTIC_DUP_THRESHOLD,
    _cosine,
    dedup_semantic,
    semantic_text,
)


def main(workspace_id: str) -> None:
    db = SessionLocal()
    try:
        rows = (
            db.query(KnowledgeItem)
            .filter(
                KnowledgeItem.workspace_id == workspace_id,
                KnowledgeItem.type.in_(["claim", "limitation"]),
                KnowledgeItem.is_deleted.is_(False),
            )
            .order_by(KnowledgeItem.paper_id)
            .all()
        )
        items: list[dict] = []
        for ki in rows:
            sp = dict(ki.source_provenance or {})
            items.append(
                {
                    "type": ki.type,
                    "canonical_name": ki.canonical_name,
                    "confidence": ki.confidence or 0.0,
                    "content": ki.content,
                    "source_provenance": sp,
                }
            )
        papers = {i["source_provenance"].get("paper_id") for i in items}
        print(f"loaded {len(items)} claim/limitation items across {len(papers)} papers")

        gw = get_embedding_gateway()
        survivors, rejected = dedup_semantic(
            items,
            embed_texts=lambda texts: gw.embed_texts(texts).embeddings,
        )
        print(
            f"dedup_semantic @{SEMANTIC_DUP_THRESHOLD}: "
            f"{len(items)} -> {len(survivors)} (rejected {len(rejected)})"
        )
        for r in rejected:
            sp = r["source_provenance"] or {}
            print(
                f"  merge: [{r['type']}] conf={r['confidence']:.2f} "
                f"paper={str(sp.get('paper_id'))[:8]} :: {semantic_text(r['content'])[:70]}"
            )

# 近似命中诊断：按（paper、type）报告 >= 0.80 的成对结果，供阈值调节。
# 复用网关（会产生额外调用）。
        print("\nnear-miss pairs (0.80 <= sim < threshold), for tuning:")
        found = False
        for paper in sorted(papers, key=lambda x: str(x)):
            paper_items = [
                i for i in items if (i["source_provenance"] or {}).get("paper_id") == paper
            ]
            for t in ("claim", "limitation"):
                group = [i for i in paper_items if i["type"] == t]
                texts = [semantic_text(i.get("content")) for i in group]
                indexable = [j for j, tx in enumerate(texts) if tx]
                if len(indexable) < 2:
                    continue
                vecs = gw.embed_texts([texts[j] for j in indexable]).embeddings
                for a in range(len(indexable)):
                    for b in range(a + 1, len(indexable)):
                        sim = _cosine(vecs[a], vecs[b])
                        if 0.80 <= sim < SEMANTIC_DUP_THRESHOLD:
                            found = True
                            ia, ib = indexable[a], indexable[b]
                            print(
                                f"  paper={str(paper)[:8]} [{t}] sim={sim:.3f}\n"
                                f"    A: {texts[ia][:64]}\n    B: {texts[ib][:64]}"
                            )
        if not found:
            print("  (none)")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True, help="workspace UUID")
    args = parser.parse_args()
    main(args.workspace_id)
