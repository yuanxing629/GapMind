"""Counter Evidence 专项校验（RG-7 / V4）。

加载 claim set（三种 claim type：A_fact / B_qualified / C_first_novel），在 live workspace
上运行 ``find_counter_evidence``，并检查原始 Recall 之外的五项行为不变量：

  1. 源论文排除 —— claim 的来源论文绝不会出现在结果中。
  2. 论文多样性 —— 存在结果时，结果应覆盖至少 2 篇不同论文。
  3. role 优先级 —— contradicts/qualifies 排在 supports/overlaps 之前。
  4. 空结果语义 —— 空 top-K 携带可区分的 ``empty_reason``（retrieval_empty / judge_failed /
     genuinely_no_counter_evidence），不能将实际系统失败伪装为“未找到”。
  5. Judge 失败信号 —— zero-confidence-unknown Judge 结果将响应标记为 ``degraded``，并保留
     诊断错误。

用法（从仓库根目录运行）：

    backend/.venv/Scripts/python.exe evaluation/retrieval/verify_counter_evidence.py \
        --workspace-id <uuid> \
        --gold evaluation/retrieval/gold/counter_evidence_v4.json

退出码 0 = 所有不变量满足；2 = 至少一项失败。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
for p in (str(BACKEND_DIR), str(REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import app.db.models  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.domains.retrieval.service import find_counter_evidence  # noqa: E402
from evaluation.retrieval.gold_set import CounterEvidenceQuery, GoldSet  # noqa: E402
from evaluation.retrieval.run_eval import resolve_paper_ref  # noqa: E402


def _paper_ids(items: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        pid = getattr(item, "paper_id", None)
        if pid and pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def _role_rank(judgement: str) -> int:
    """数值越小优先级越高；与 service.py 中的 COUNTER_ROLE_PRIORITY 对应。"""
    return {"contradicts": 0, "qualifies": 1, "supports": 2, "overlaps": 2, "unknown": 3}.get(
        judgement, 99
    )


def check_claim(db, workspace_id: str, q: CounterEvidenceQuery, top_k: int, minimal: bool) -> dict[str, Any]:
    source = resolve_paper_ref(db, workspace_id, q.source_paper_ref)
    if source is None:
        return {"query_id": q.query_id, "claim_type": q.claim_type, "error": f"unresolved source: {q.source_paper_ref}", "passed": False}

    resp = find_counter_evidence(
        workspace_id,
        q.claim_text,
        top_k=top_k,
        use_reranker=True,
        use_judge=not minimal,
        exclude_paper_ids={source.id},
    )

    pids = _paper_ids(resp.items)
    roles = [getattr(i, "judgement", "unknown") for i in resp.items]
    result: dict[str, Any] = {
        "query_id": q.query_id,
        "claim_type": q.claim_type,
        "status": resp.status,
        "count": len(resp.items),
        "paper_ids": pids,
        "roles": roles,
        "empty_reason": resp.empty_reason,
        "source_paper_id": source.id,
        "passed": True,
        "checks": {},
    }

# 1. 源论文排除
    src_ok = source.id not in pids
    result["checks"]["source_excluded"] = src_ok

# 2. 论文多样性：结果数 >= 2 时，应覆盖 >= 2 篇不同论文
#（一篇论文的分块不能占满 counter-evidence 视图）。
    raw_count = len(resp.items)
    div_ok = (len(set(pids)) >= 2) if raw_count >= 2 else True
    result["checks"]["paper_diversity"] = div_ok

# 3. 角色优先级：contradicts/qualifies 在 supports/overlaps/unknown 之前
    role_ok = True
    for i in range(len(roles)):
        for j in range(i + 1, len(roles)):
            if _role_rank(roles[i]) > _role_rank(roles[j]):
                role_ok = False
                break
    result["checks"]["role_priority"] = role_ok

# 4. 空结果语义
    empty_ok = True
    if not resp.items:
# 必须区分空 Top-K，不能伪造为“没有 counter-evidence”。
        if resp.empty_reason is None:
            empty_ok = False
        elif resp.status == "failed":
            empty_ok = False  # system failure is not a clean "found nothing"
    else:
# 非空结果：empty_reason 可以为 None（找到反证），也可以有值（只找到
# supports/overlaps → genuinely_no_counter_evidence）。
        if resp.empty_reason == "judge_failed" and resp.status != "degraded":
            empty_ok = False
    result["checks"]["empty_semantics"] = empty_ok

# 5. Judge 失败信号
    judge_ok = True
    if resp.status == "degraded":
# degraded 表示存在零置信度 unknown（Judge 失败），保留错误信号。
        judge_ok = any(r == "unknown" for r in roles)
    result["checks"]["judge_failure_signal"] = judge_ok

    result["passed"] = all(result["checks"].values())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=False)
    parser.add_argument("--gold", type=str, required=True)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--minimal", action="store_true", help="Skip LLM judge (not for the real Gate).")
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    gold_path = Path(args.gold)
    if not gold_path.exists():
        print(f"Gold set not found: {gold_path}")
        return 1
    gold = GoldSet.model_validate(json.loads(gold_path.read_text(encoding="utf-8")))

    workspace_id = args.workspace_id or gold.workspace_hint
    if not workspace_id:
        print("No workspace_id. Pass --workspace-id or set workspace_hint in gold.")
        return 1

    db = SessionLocal()
    try:
        claims = gold.counter_evidence
        if not claims:
            print("Gold set has no counter_evidence queries.")
            return 1
        by_type: dict[str, list[dict[str, Any]]] = {}
        for q in claims:
            result = check_claim(db, workspace_id, q, args.top_k, args.minimal)
            by_type.setdefault(q.claim_type or "untyped", []).append(result)

        report: dict[str, Any] = {
            "schema_version": "1.0.0",
            "case_id": gold.case_id,
            "corpus_version": gold.corpus_version,
            "workspace_id": workspace_id,
            "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "top_k": args.top_k,
            "minimal": args.minimal,
            "by_claim_type": by_type,
            "summary": {},
        }

        print("=== Counter Evidence V4 verification ===")
        overall = True
        for claim_type, results in by_type.items():
            checks = ["source_excluded", "paper_diversity", "role_priority", "empty_semantics", "judge_failure_signal"]
            per_check: dict[str, int] = {}
            for c in checks:
                per_check[c] = sum(1 for r in results if r["checks"].get(c))
            total = len(results)
            print(f"\n[{claim_type}] {total} claims")
            for c in checks:
                mark = "PASS" if per_check[c] == total else "FAIL"
                print(f"  {mark} {c}: {per_check[c]}/{total}")
                if per_check[c] != total:
                    overall = False
            report["summary"][claim_type] = {"total": total, "per_check": per_check}

        report["gate_passed"] = overall
        print(f"\nOverall: {'PASS' if overall else 'FAIL'}")

        output_path = Path(args.output) if args.output else (
            Path(__file__).parent / "reports" / f"{gold.case_id}_{time.strftime('%Y%m%d_%H%M%S')}.json"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Report saved to: {output_path}")
        return 0 if overall else 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
