"""MA-12（第 2 部分）：跳过外部选择，恢复到 synthesis + Critic。"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402

import app.db.models  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.domains.agent.models import AgentRun, AgentStep  # noqa: E402
from app.domains.discover.models import ResearchOpportunity  # noqa: E402
from app.domains.discover.service import DiscoverService  # noqa: E402

RUN_ID = "bd6db54a-ff6a-41d8-9fd3-15686a66815f"


def main() -> int:
    db = SessionLocal()
    try:
        service = DiscoverService(db)
        run = service.get_run("123100ea-e75b-4110-9048-1f5b92668c32", RUN_ID)
        print(f"Before: status={run.status} stage={run.stage}")
        service.skip_external_selection("123100ea-e75b-4110-9048-1f5b92668c32", RUN_ID, actor="verification")
        db.refresh(run)
        print(f"Skipped external selection → status={run.status} stage={run.stage}")

        result = service.execute_run(RUN_ID)
        print(f"execute_run (resume): status={result.get('status')}")

        db.refresh(run)
        print("\n=== Discover run final ===")
        print(f"status={run.status} stage={run.stage} verification_status={run.verification_status} progress={run.progress}")

        target = db.scalar(
            select(AgentRun).where(AgentRun.agent_type == "discover").order_by(AgentRun.created_at.desc()).limit(1)
        )
        if target:
            print(f"\n=== AgentRun {target.id} status={target.status} stage={target.current_stage} ===")
            steps = list(db.execute(select(AgentStep).where(AgentStep.run_id == target.id).order_by(AgentStep.sequence)).scalars())
            print(f"=== AgentStep handoff ({len(steps)}) ===")
            for s in steps:
                extra = ""
                if s.stage == "critic" and s.details:
                    v = s.details.get("verdicts")
                    if v:
                        extra = f" verdicts={v}"
                print(f"  {s.sequence:>2}. [{s.stage:<18}] {s.status:<10} {s.summary[:90]}{extra}")

        opps = list(db.execute(select(ResearchOpportunity).where(ResearchOpportunity.discover_run_id == RUN_ID, ResearchOpportunity.is_deleted.is_(False))).scalars())
        print(f"\n=== Opportunities ({len(opps)}) ===")
        for o in opps:
            sp = o.source_payload or {}
            critic = sp.get("critic_review") or {}
            narrowing = sp.get("narrowing_pass") or {}
            print(f"  - {o.title[:72]}")
            print(f"      status={o.status} conf={o.confidence:.2f} gate_verified={bool((sp.get('gate') or {}).get('verified'))}")
            if critic:
                print(f"      critic={critic.get('verdict')} challenges={len(critic.get('challenges') or [])} narrowing={narrowing.get('outcome') if narrowing else '-'}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
