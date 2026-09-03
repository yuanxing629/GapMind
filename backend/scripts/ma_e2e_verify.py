"""MA-12 real end-to-end validation: run a Discover run against the real DB.

Executes the multi-agent orchestration (AgentStep handoff + CriticAgent +
narrowing loop) on real PostgreSQL + Milvus + LLM, then dumps the resulting
AgentRun/AgentStep sequence and opportunity critic reviews for inspection.

Run from backend/ (needs .env for REMOTE / SILICONFLOW keys).
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402

import app.db.models  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.domains.agent.models import AgentRun, AgentStep  # noqa: E402
from app.domains.discover.models import ResearchOpportunity  # noqa: E402
from app.domains.discover.schemas import DiscoverInput, DiscoverRunCreateRequest  # noqa: E402
from app.domains.discover.service import DiscoverService  # noqa: E402

WORKSPACE_ID = "123100ea-e75b-4110-9048-1f5b92668c32"
TOPIC = (
    "Are self-interpretable graph neural network explanations faithful, stable, "
    "and predictively useful under graph distribution shift?"
)


def main() -> int:
    db = SessionLocal()
    try:
        service = DiscoverService(db)
        run, _ = service.create_run(
            WORKSPACE_ID,
            DiscoverRunCreateRequest(
                input=DiscoverInput(topic=TOPIC, keywords=["graph neural network explanation", "distribution shift"]),
                config={"top_k": 10, "max_opportunities": 3},
            ),
            actor="verification",
        )
        run_id = run.id
        print(f"Created Discover run {run_id}")
        result = service.execute_run(run_id)
        print(f"execute_run result: status={result.get('status')} waiting_for_user={result.get('waiting_for_user', False)}")

        db.refresh(run)
        print("\n=== Discover run final state ===")
        print(f"status={run.status} stage={run.stage} verification_status={run.verification_status} progress={run.progress}")

        agent_runs = list(
            db.execute(select(AgentRun).where(AgentRun.agent_type == "discover").order_by(AgentRun.created_at.desc()).limit(10)).scalars()
        )
        target = next((ar for ar in agent_runs if (ar.input_payload or {}).get("discover_run_id") == run_id), None)
        if target is None and agent_runs:
            target = agent_runs[0]
        if target is None:
            print("\n[WARN] no AgentRun found for this run")
        else:
            print(f"\n=== AgentRun ({target.id}) ===")
            print(f"agent_type={target.agent_type} status={target.status} current_stage={target.current_stage} progress={target.progress}")
            steps = list(db.execute(select(AgentStep).where(AgentStep.run_id == target.id).order_by(AgentStep.sequence)).scalars())
            print(f"\n=== AgentStep handoff ({len(steps)} steps) ===")
            for s in steps:
                summary = s.summary[:100]
                verdicts = ""
                if s.stage == "critic" and s.details:
                    v = s.details.get("verdicts")
                    if v:
                        verdicts = f" verdicts={v}"
                print(f"  {s.sequence:>2}. [{s.stage:<18}] {s.status:<10} {summary}{verdicts}")

        opps = list(db.execute(select(ResearchOpportunity).where(ResearchOpportunity.discover_run_id == run_id, ResearchOpportunity.is_deleted.is_(False))).scalars())
        print(f"\n=== Opportunities ({len(opps)}) ===")
        for o in opps:
            sp = o.source_payload or {}
            critic = sp.get("critic_review") or {}
            narrowing = sp.get("narrowing_pass") or {}
            print(f"  - {o.title[:70]}")
            print(f"      status={o.status} confidence={o.confidence:.2f} gate_verified={bool((sp.get('gate') or {}).get('verified'))}")
            if critic:
                print(f"      critic={critic.get('verdict')} challenges={len(critic.get('challenges') or [])}")
            if narrowing:
                print(f"      narrowing={narrowing.get('outcome')} counter_candidates={narrowing.get('counter_candidates')}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
