"""扫描论文的 parsed_markdown，查找与主张相关的段落。

这是 RG-8 反证 Gold 确认的人工复核辅助工具
（docs/rg8_counter_evidence_gold_review.md）。它找到目标论文中最可能
支持/限定/反驳给定主张的段落，使人可以快速判断 Gold 标注是否正确，
无需阅读整篇论文。

方法：朴素关键词重叠。将主张拆分为关键词（移除停用词并统一大小写），
然后根据每个 Markdown 段落包含的不同关键词数量评分。打印排名靠前的段落及章节上下文和字符偏移，
便于复核者跳转到原文。

该工具刻意保持简单——它是*定位器*而不是判断器。段落是否真的限定/反驳主张，仍由人工决定。

用法（从 backend/ 目录运行）：

    .venv/Scripts/python.exe scripts/evidence_scan.py \
        --workspace-id 123100ea-e75b-4110-9048-1f5b92668c32 \
        --claim "Adding more explanation constraints always improves prediction accuracy." \
        --paper-ref "VGIB"
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

REPO_ROOT = BACKEND_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import app.db.models  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.domains.artifact.models import Artifact  # noqa: E402
from app.domains.artifact.service import ArtifactService  # noqa: E402
from evaluation.retrieval.run_eval import resolve_paper_ref  # noqa: E402

_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "of", "for", "with", "on", "in",
    "at", "by", "to", "is", "are", "was", "were", "be", "been", "being",
    "it", "its", "this", "that", "these", "those", "always", "never",
    "more", "most", "all", "any", "our", "we", "their", "them", "they",
    "than", "from", "as", "into", "such", "can", "may", "might", "would",
    "could", "should", "will", "does", "do", "not", "no", "improves",
    "prediction", "accuracy", "explanation", "explanations",
})


def _keywords(claim: str) -> list[str]:
    return [
        w for w in re.findall(r"[a-zA-Z][a-zA-Z-]{2,}", claim.casefold())
        if w not in _STOPWORDS
    ]


def _split_paragraphs(md: str) -> list[tuple[int, str]]:
    """将 Markdown 拆分为（char_offset、text）段落。"""
    out: list[tuple[int, str]] = []
    for m in re.finditer(r"(?m)^[^\n]+(?:\n(?![#\n])[^\n]+)*", md):
        text = m.group(0).strip()
        if len(text) < 20:  # skip headings / noise
            continue
        out.append((m.start(), text))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--claim", required=True)
    parser.add_argument("--paper-ref", required=True)
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        paper = resolve_paper_ref(db, args.workspace_id, args.paper_ref)
        if paper is None:
            print(f"paper not resolved in workspace: {args.paper_ref}")
            print("(title may be abbreviated — check exact title first)")
            return 1
        if not paper.parsed_markdown_artifact_id:
            print(f"paper has no parsed_markdown: {paper.title}")
            return 1

        md_artifact = db.get(Artifact, paper.parsed_markdown_artifact_id)
        md_path = ArtifactService(db).resolve_abs_path(md_artifact)
        md = md_path.read_text(encoding="utf-8")

        keywords = _keywords(args.claim)
        print(f"paper : {paper.title}")
        print(f"claim : {args.claim}")
        print(f"keywords: {keywords}")
        print(f"markdown: {md_path} ({len(md)} chars)")
        print()

        paragraphs = _split_paragraphs(md)
        scored = []
        for offset, text in paragraphs:
            text_l = text.casefold()
            hits = [k for k in keywords if k in text_l]
            if hits:
                scored.append((len(hits), offset, text, hits))

        scored.sort(key=lambda x: x[0], reverse=True)
        print(f"== Top {args.top} most relevant passages ==")
        for rank, (n_hits, offset, text, hits) in enumerate(scored[: args.top], 1):
# 查找此偏移之前的章节标题
            heading = ""
            for hm in re.finditer(r"(?m)^##+\s+(.*)", md):
                if hm.start() < offset:
                    heading = hm.group(1).strip()
                else:
                    break
            print(f"\n--- #{rank} (hits={n_hits}: {', '.join(hits)}) offset={offset} section=[{heading}] ---")
            print(text[:1200])
            print("...")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
