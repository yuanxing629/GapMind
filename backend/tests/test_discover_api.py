"""持久化 Discover Agent API 契约测试。"""

from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domains.discover.models import (
    DiscoverExternalCandidate,
    DiscoverRun,
    OpportunityVersion,
    ResearchOpportunity,
    ResearchPlan,
)
from app.domains.task.models import Task


def test_create_and_read_discover_run(client: TestClient) -> None:
    workspace = client.post("/api/v1/workspaces", json={"name": "Discover WS"}).json()
    with patch("app.domains.discover.router.spawn_discover_task", return_value="celery-test-id"):
        response = client.post(
            f"/api/v1/workspaces/{workspace['id']}/discover/runs",
            json={
                "input": {"topic": "Robust graph learning under distribution shift"},
                "scope": {"year_from": 2020, "year_to": 2026},
                "config": {"max_opportunities": 3, "top_k": 10},
            },
        )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["task_id"]

    runs = client.get(f"/api/v1/workspaces/{workspace['id']}/discover/runs")
    assert runs.status_code == 200, runs.text
    assert runs.json()["total"] == 1
    run_id = runs.json()["items"][0]["id"]

    detail = client.get(f"/api/v1/workspaces/{workspace['id']}/discover/runs/{run_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["input_topic"].startswith("Robust graph")
    assert detail.json()["external_candidates"] == []


def test_discover_run_validates_workspace_scope(client: TestClient) -> None:
    first = client.post("/api/v1/workspaces", json={"name": "A"}).json()
    second = client.post("/api/v1/workspaces", json={"name": "B"}).json()
    response = client.post(
        f"/api/v1/workspaces/{first['id']}/discover/runs",
        json={"input": {"topic": "topic", "paper_ids": [second['id']]}},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "discover_input_invalid"


def test_discover_run_delete_hides_history_and_preserves_run_outputs(
    client: TestClient,
    db_session: Session,
) -> None:
    workspace = client.post("/api/v1/workspaces", json={"name": "Delete Discover WS"}).json()
    with patch("app.domains.discover.router.spawn_discover_task", return_value="celery-test-id"):
        created = client.post(
            f"/api/v1/workspaces/{workspace['id']}/discover/runs",
            json={"input": {"topic": "Topic to delete"}},
        )
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]

    run = db_session.get(DiscoverRun, run_id)
    assert run is not None
    run.status = "succeeded"
    db_session.commit()

    deleted = client.delete(f"/api/v1/workspaces/{workspace['id']}/discover/runs/{run_id}")
    assert deleted.status_code == 204, deleted.text

    history = client.get(f"/api/v1/workspaces/{workspace['id']}/discover/runs")
    assert history.status_code == 200, history.text
    assert history.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}

    detail = client.get(f"/api/v1/workspaces/{workspace['id']}/discover/runs/{run_id}")
    assert detail.status_code == 404, detail.text
    assert detail.json()["detail"]["error"] == "discover_run_not_found"


def test_active_discover_run_cannot_be_deleted(client: TestClient) -> None:
    workspace = client.post("/api/v1/workspaces", json={"name": "Active Delete Discover WS"}).json()
    with patch("app.domains.discover.router.spawn_discover_task", return_value="celery-test-id"):
        created = client.post(
            f"/api/v1/workspaces/{workspace['id']}/discover/runs",
            json={"input": {"topic": "Active topic"}},
        )
    assert created.status_code == 202, created.text

    response = client.delete(
        f"/api/v1/workspaces/{workspace['id']}/discover/runs/{created.json()['run_id']}"
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["error"] == "discover_run_deletion_conflict"


def test_pending_opportunity_filter_returns_authoritative_workspace_count(
    client: TestClient,
    db_session: Session,
) -> None:
    workspace = client.post("/api/v1/workspaces", json={"name": "Opportunity Count WS"}).json()
    db_session.add_all(
        [
            ResearchOpportunity(
                workspace_id=workspace["id"],
                title="Candidate opportunity",
                summary="Summary",
                rationale="Rationale",
                status="candidate",
            ),
            ResearchOpportunity(
                workspace_id=workspace["id"],
                title="Needs evidence opportunity",
                summary="Summary",
                rationale="Rationale",
                status="needs_more_evidence",
            ),
            ResearchOpportunity(
                workspace_id=workspace["id"],
                title="Confirmed opportunity",
                summary="Summary",
                rationale="Rationale",
                status="confirmed",
            ),
        ]
    )
    db_session.commit()

    with patch("app.domains.discover.router.spawn_discover_task", return_value="celery-test-id"):
        created_run = client.post(
            f"/api/v1/workspaces/{workspace['id']}/discover/runs",
            json={"input": {"topic": "Deleted run topic"}},
        )
    assert created_run.status_code == 202, created_run.text
    deleted_run = db_session.get(DiscoverRun, created_run.json()["run_id"])
    assert deleted_run is not None
    deleted_run.deleted_at = datetime.now(timezone.utc)
    db_session.add(
        ResearchOpportunity(
            workspace_id=workspace["id"],
            discover_run_id=deleted_run.id,
            title="Deleted run opportunity",
            summary="Summary",
            rationale="Rationale",
            status="candidate",
        )
    )
    db_session.commit()

    all_response = client.get(
        f"/api/v1/workspaces/{workspace['id']}/discover/opportunities",
        params={"limit": 100},
    )
    assert all_response.status_code == 200, all_response.text
    assert all_response.json()["total"] == 3

    response = client.get(
        f"/api/v1/workspaces/{workspace['id']}/discover/opportunities",
        params={"pending_only": "true", "limit": 1},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] in {"candidate", "needs_more_evidence"}


def test_user_can_select_multiple_external_papers_in_one_batch(
    client: TestClient,
    db_session: Session,
) -> None:
    workspace = client.post("/api/v1/workspaces", json={"name": "Batch Selection WS"}).json()
    with patch("app.domains.discover.router.spawn_discover_task", return_value="initial-celery-id"):
        created = client.post(
            f"/api/v1/workspaces/{workspace['id']}/discover/runs",
            json={"input": {"topic": "Batch external verification"}},
        )
    run = db_session.get(DiscoverRun, created.json()["run_id"])
    assert run is not None and run.task_id
    task = db_session.get(Task, run.task_id)
    assert task is not None
    run.status = "waiting_for_user"
    run.stage = "external_selection"
    run.progress = 0.62
    task.status = "waiting_for_user"
    candidates = [
        DiscoverExternalCandidate(
            discover_run_id=run.id,
            query="batch verification",
            rank=index,
            external_paper_id=f"S2-batch-{index}",
            title=f"External candidate {index}",
            authors=[],
            snapshot_payload={},
        )
        for index in (1, 2)
    ]
    db_session.add_all(candidates)
    db_session.commit()

    with patch("app.domains.discover.router.spawn_discover_task", return_value="batch-celery-id"):
        response = client.post(
            f"/api/v1/workspaces/{workspace['id']}/discover/runs/{run.id}/external-selection",
            json={"candidate_ids": [candidate.id for candidate in candidates], "action": "import_and_verify"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["stage"] == "fulltext_verification"
    assert body["stage_summaries"]["external_selection"]["selected"] == 2
    for candidate in candidates:
        db_session.refresh(candidate)
        assert candidate.verification_status == "selected"
    db_session.refresh(task)
    assert task.status == "running"
    assert task.celery_task_id == "batch-celery-id"


def test_user_can_skip_external_selection_and_resume_run(
    client: TestClient,
    db_session: Session,
) -> None:
    workspace = client.post("/api/v1/workspaces", json={"name": "Skip Selection WS"}).json()
    with patch("app.domains.discover.router.spawn_discover_task", return_value="initial-celery-id"):
        created = client.post(
            f"/api/v1/workspaces/{workspace['id']}/discover/runs",
            json={"input": {"topic": "Workspace-only evidence"}},
        )
    run = db_session.get(DiscoverRun, created.json()["run_id"])
    assert run is not None and run.task_id
    task = db_session.get(Task, run.task_id)
    assert task is not None
    run.status = "waiting_for_user"
    run.stage = "external_selection"
    run.progress = 0.62
    run.stage_summaries = {
        "external_search": {"status": "succeeded", "external_candidates": 1},
        "external_selection": {"status": "waiting_for_user"},
    }
    task.status = "waiting_for_user"
    db_session.add(
        DiscoverExternalCandidate(
            discover_run_id=run.id,
            query="workspace evidence",
            rank=1,
            external_paper_id="S2-skip",
            title="External candidate",
            authors=[],
            snapshot_payload={},
        )
    )
    db_session.commit()

    with patch("app.domains.discover.router.spawn_discover_task", return_value="resumed-celery-id"):
        response = client.post(
            f"/api/v1/workspaces/{workspace['id']}/discover/runs/{run.id}/external-selection/skip"
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["stage"] == "synthesis"
    assert body["stage_summaries"]["external_selection"]["status"] == "skipped"
    db_session.refresh(task)
    assert task.status == "running"
    assert task.payload["user_decision"]["action"] == "skip_external_selection"


def test_research_portfolio_keeps_confirmed_outputs_from_deleted_run(
    client: TestClient,
    db_session: Session,
) -> None:
    workspace = client.post("/api/v1/workspaces", json={"name": "Research Portfolio WS"}).json()
    run = DiscoverRun(
        workspace_id=workspace["id"],
        input_topic="Archived discovery",
        input_payload={},
        scope={},
        config={},
        status="succeeded",
        stage="saved",
        progress=1.0,
        verification_status="complete",
        stage_summaries={},
        deleted_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.flush()
    opportunity = ResearchOpportunity(
        workspace_id=workspace["id"],
        discover_run_id=run.id,
        title="Confirmed durable opportunity",
        summary="Summary",
        rationale="Rationale",
        confidence=0.8,
        status="confirmed",
    )
    db_session.add(opportunity)
    db_session.flush()
    version = OpportunityVersion(
        opportunity_id=opportunity.id,
        version_number=1,
        title=opportunity.title,
        problem_statement="Problem",
        candidate_research_question="Can the method generalize?",
        candidate_hypothesis="The method improves robustness.",
        confidence=0.8,
        evidence_coverage=0.75,
        verification_status="verified",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(version)
    db_session.flush()
    opportunity.current_version_id = version.id
    plan = ResearchPlan(
        workspace_id=workspace["id"],
        opportunity_id=opportunity.id,
        opportunity_version_id=version.id,
        research_question=version.candidate_research_question,
        hypothesis=version.candidate_hypothesis,
    )
    db_session.add(plan)
    db_session.commit()

    portfolio = client.get(
        f"/api/v1/workspaces/{workspace['id']}/discover/portfolio/opportunities"
    )
    assert portfolio.status_code == 200, portfolio.text
    assert portfolio.json()["total"] == 1
    assert portfolio.json()["items"][0]["opportunity"]["id"] == opportunity.id
    assert portfolio.json()["items"][0]["current_version"]["id"] == version.id
    assert portfolio.json()["items"][0]["plan"]["id"] == plan.id

    plans = client.get(f"/api/v1/workspaces/{workspace['id']}/discover/plans")
    assert plans.status_code == 200, plans.text
    assert plans.json()["total"] == 1
    assert plans.json()["items"][0]["id"] == plan.id
