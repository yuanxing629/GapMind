"""Discover Agent HTTP API.

Domain exceptions raised here (DiscoverInputError, DiscoverRunNotFoundError,
OpportunityNotFoundError, OpportunityVersionConflict,
InvalidOpportunityTransition, DiscoverGateError, WorkspaceNotFoundError)
are translated into HTTP responses by the central handler registered in
``app.core.exception_handlers``.

Every endpoint sits under ``/workspaces/{workspace_id}/discover/...`` and
therefore needs the workspace to exist. The check is wired in once via the
``APIRouter(dependencies=...)`` argument so endpoints stay free of an
inline ``workspace_service.get(workspace_id)`` line.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, get_owned_workspace
from app.domains.discover.schemas import (
    ConfirmRequest,
    DecisionRequest,
    DiscoverRunCreateRequest,
    DiscoverRunCreateResponse,
    DiscoverRunDetail,
    AgentStepRead,
    DiscoverRunListResponse,
    DiscoverRunRead,
    EditConfirmRequest,
    ExternalSelectionRequest,
    OpportunityDetail,
    OpportunityEvidenceRead,
    OpportunityEvidenceContext,
    OpportunityListResponse,
    OpportunityPortfolioItem,
    OpportunityPortfolioResponse,
    OpportunityVersionRead,
    PlanCreateResponse,
    ResearchOpportunityRead,
    ResearchPlanRead,
    ResearchPlanListResponse,
)
from app.domains.discover.service import DiscoverService
from app.domains.task.service import TaskService
from app.workers.tasks.run_discover import spawn_discover_task


def _service(db: Session = Depends(get_db)) -> DiscoverService:
    return DiscoverService(db)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/discover",
    tags=["discover"],
    dependencies=[Depends(get_owned_workspace)],
)


def _attach_celery_id(db: Session, task_id: str, celery_id: str) -> None:
    task = TaskService(db).get(task_id)
    task.celery_task_id = celery_id
    db.commit()


@router.post(
    "/runs", response_model=DiscoverRunCreateResponse, status_code=status.HTTP_202_ACCEPTED
)
def create_run(
    workspace_id: str,
    payload: DiscoverRunCreateRequest,
    service: DiscoverService = Depends(_service),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> DiscoverRunCreateResponse:
    run, task_id = service.create_run(workspace_id, payload, actor=current_user)
    celery_id = spawn_discover_task(run.id)
    _attach_celery_id(db, task_id, celery_id)
    return DiscoverRunCreateResponse(run_id=run.id, task_id=task_id, status=run.status)


@router.get("/runs", response_model=DiscoverRunListResponse)
def list_runs(
    workspace_id: str,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: DiscoverService = Depends(_service),
) -> DiscoverRunListResponse:
    items, total = service.list_runs(
        workspace_id, status_filter=status_filter, limit=limit, offset=offset
    )
    return DiscoverRunListResponse(
        items=[DiscoverRunRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}", response_model=DiscoverRunDetail)
def get_run(
    workspace_id: str,
    run_id: str,
    service: DiscoverService = Depends(_service),
) -> DiscoverRunDetail:
    data = service.run_detail(workspace_id, run_id)
    return DiscoverRunDetail(
        **DiscoverRunRead.model_validate(data["run"]).model_dump(),
        external_candidates=[item for item in data["external_candidates"]],
        opportunities=[ResearchOpportunityRead.model_validate(item) for item in data["opportunities"]],
        agent_steps=[AgentStepRead.model_validate(item) for item in data["agent_steps"]],
    )


@router.post("/runs/{run_id}/external-selection", response_model=DiscoverRunRead)
def select_external(
    workspace_id: str,
    run_id: str,
    payload: ExternalSelectionRequest,
    service: DiscoverService = Depends(_service),
    db: Session = Depends(get_db),
) -> DiscoverRunRead:
    run = service.select_external(workspace_id, run_id, payload.candidate_ids)
    celery_id = spawn_discover_task(run.id)
    if run.task_id:
        _attach_celery_id(db, run.task_id, celery_id)
    return DiscoverRunRead.model_validate(run)


@router.post("/runs/{run_id}/external-selection/skip", response_model=DiscoverRunRead)
def skip_external_selection(
    workspace_id: str,
    run_id: str,
    service: DiscoverService = Depends(_service),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> DiscoverRunRead:
    run = service.skip_external_selection(workspace_id, run_id, actor=current_user)
    celery_id = spawn_discover_task(run.id)
    if run.task_id:
        _attach_celery_id(db, run.task_id, celery_id)
    return DiscoverRunRead.model_validate(run)


@router.post("/runs/{run_id}/cancel", response_model=DiscoverRunRead)
def cancel_run(
    workspace_id: str,
    run_id: str,
    service: DiscoverService = Depends(_service),
) -> DiscoverRunRead:
    return DiscoverRunRead.model_validate(service.cancel_run(workspace_id, run_id))


@router.delete("/runs/{run_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def delete_run(
    workspace_id: str,
    run_id: str,
    service: DiscoverService = Depends(_service),
    current_user: str = Depends(get_current_user),
) -> None:
    service.delete_run(workspace_id, run_id, actor=current_user)


@router.get("/opportunities", response_model=OpportunityListResponse)
def list_opportunities(
    workspace_id: str,
    status_filter: str | None = Query(None, alias="status"),
    run_id: str | None = None,
    pending_only: bool = Query(
        False, description="Only return opportunities awaiting human handling"
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: DiscoverService = Depends(_service),
) -> OpportunityListResponse:
    items, total = service.list_opportunities(
        workspace_id,
        status_filter=status_filter,
        run_id=run_id,
        pending_only=pending_only,
        limit=limit,
        offset=offset,
    )
    return OpportunityListResponse(
        items=[ResearchOpportunityRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/portfolio/opportunities", response_model=OpportunityPortfolioResponse)
def list_confirmed_portfolio(
    workspace_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: DiscoverService = Depends(_service),
) -> OpportunityPortfolioResponse:
    items, total = service.list_confirmed_portfolio(
        workspace_id,
        limit=limit,
        offset=offset,
    )
    return OpportunityPortfolioResponse(
        items=[
            OpportunityPortfolioItem(
                opportunity=ResearchOpportunityRead.model_validate(item["opportunity"]),
                current_version=OpportunityVersionRead.model_validate(item["current_version"])
                if item["current_version"]
                else None,
                plan=ResearchPlanRead.model_validate(item["plan"]) if item["plan"] else None,
            )
            for item in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/plans", response_model=ResearchPlanListResponse)
def list_research_plans(
    workspace_id: str,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: DiscoverService = Depends(_service),
) -> ResearchPlanListResponse:
    items, total = service.list_research_plans(
        workspace_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )
    return ResearchPlanListResponse(
        items=[ResearchPlanRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/opportunities/{opportunity_id}", response_model=OpportunityDetail)
def get_opportunity(
    workspace_id: str,
    opportunity_id: str,
    service: DiscoverService = Depends(_service),
) -> OpportunityDetail:
    data = service.opportunity_detail(workspace_id, opportunity_id)
    return OpportunityDetail(
        opportunity=ResearchOpportunityRead.model_validate(data["opportunity"]),
        current_version=OpportunityVersionRead.model_validate(data["current_version"])
        if data["current_version"]
        else None,
        versions=[OpportunityVersionRead.model_validate(item) for item in data["versions"]],
        evidence=[OpportunityEvidenceRead.model_validate(item) for item in data["evidence"]],
        decisions=data["decisions"],
        plan=ResearchPlanRead.model_validate(data["plan"]) if data["plan"] else None,
    )


@router.get("/opportunities/{opportunity_id}/versions", response_model=list[OpportunityVersionRead])
def list_versions(
    workspace_id: str,
    opportunity_id: str,
    service: DiscoverService = Depends(_service),
) -> list[OpportunityVersionRead]:
    return [
        OpportunityVersionRead.model_validate(item)
        for item in service.versions(workspace_id, opportunity_id)
    ]


@router.get("/evidence/{evidence_id}/context", response_model=OpportunityEvidenceContext)
def get_evidence_context(
    workspace_id: str,
    evidence_id: str,
    service: DiscoverService = Depends(_service),
) -> OpportunityEvidenceContext:
    data = service.opportunity_evidence_context(workspace_id, evidence_id)
    return OpportunityEvidenceContext(
        evidence=OpportunityEvidenceRead.model_validate(data["evidence"]),
        available=data["available"],
        paper_id=data["paper_id"],
        artifact_id=data["artifact_id"],
        artifact_kind=data["artifact_kind"],
        filename=data["filename"],
        content=data["content"],
        start_char=data["start_char"],
        end_char=data["end_char"],
        message=data["message"],
    )


@router.post("/opportunities/{opportunity_id}/confirm", response_model=ResearchOpportunityRead)
def confirm(
    workspace_id: str,
    opportunity_id: str,
    payload: ConfirmRequest,
    service: DiscoverService = Depends(_service),
    current_user: str = Depends(get_current_user),
) -> ResearchOpportunityRead:
    return ResearchOpportunityRead.model_validate(
        service.confirm(
            workspace_id, opportunity_id, payload.version_id, payload.note, actor=current_user
        )
    )


@router.post("/opportunities/{opportunity_id}/reassess", response_model=ResearchOpportunityRead)
def reassess_opportunity_gate(
    workspace_id: str,
    opportunity_id: str,
    service: DiscoverService = Depends(_service),
    current_user: str = Depends(get_current_user),
) -> ResearchOpportunityRead:
    return ResearchOpportunityRead.model_validate(
        service.reassess_opportunity_gate(
            workspace_id,
            opportunity_id,
            actor=current_user,
        )
    )


@router.patch("/opportunities/{opportunity_id}", response_model=ResearchOpportunityRead)
def edit_confirm(
    workspace_id: str,
    opportunity_id: str,
    payload: EditConfirmRequest,
    service: DiscoverService = Depends(_service),
    current_user: str = Depends(get_current_user),
) -> ResearchOpportunityRead:
    return ResearchOpportunityRead.model_validate(
        service.edit_confirm(
            workspace_id,
            opportunity_id,
            payload.base_version_id,
            payload.changes,
            payload.note,
            actor=current_user,
        )
    )


@router.post("/opportunities/{opportunity_id}/reject", response_model=ResearchOpportunityRead)
def reject(
    workspace_id: str,
    opportunity_id: str,
    payload: DecisionRequest,
    service: DiscoverService = Depends(_service),
    current_user: str = Depends(get_current_user),
) -> ResearchOpportunityRead:
    return ResearchOpportunityRead.model_validate(
        service.reject(workspace_id, opportunity_id, payload.note, actor=current_user)
    )


@router.post("/opportunities/{opportunity_id}/defer", response_model=ResearchOpportunityRead)
def defer(
    workspace_id: str,
    opportunity_id: str,
    payload: DecisionRequest,
    service: DiscoverService = Depends(_service),
    current_user: str = Depends(get_current_user),
) -> ResearchOpportunityRead:
    return ResearchOpportunityRead.model_validate(
        service.defer(
            workspace_id, opportunity_id, payload.note, payload.defer_condition, actor=current_user
        )
    )


@router.post("/opportunities/{opportunity_id}/convert", response_model=PlanCreateResponse)
def convert(
    workspace_id: str,
    opportunity_id: str,
    service: DiscoverService = Depends(_service),
    current_user: str = Depends(get_current_user),
) -> PlanCreateResponse:
    return PlanCreateResponse(
        plan=ResearchPlanRead.model_validate(
            service.convert_to_plan(workspace_id, opportunity_id, actor=current_user)
        )
    )
