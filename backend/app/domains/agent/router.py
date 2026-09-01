"""Workspace-scoped Agent API."""

from __future__ import annotations

import io
import json
import zipfile
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_owned_workspace
from app.domains.agent.schemas import (
    AgentConfirmResponse,
    AgentRunCreate,
    AgentRunDetail,
    AgentRunListResponse,
    AgentRunRead,
)
from app.domains.agent.service import AgentService
from app.domains.agent.service import AgentInputError
from app.domains.discover.models import ResearchPlan
from app.domains.task.models import Task
from app.workers.tasks.run_agent import spawn_agent_task


router = APIRouter(
    prefix="/workspaces/{workspace_id}/agent-runs",
    tags=["agent"],
    dependencies=[Depends(get_owned_workspace)],
)


def _service(db: Session = Depends(get_db)) -> AgentService:
    return AgentService(db)


def _detail(service: AgentService, workspace_id: str, run_id: str) -> AgentRunDetail:
    run, steps, artifacts = service.detail(workspace_id, run_id)
    data = AgentRunRead.model_validate(run).model_dump()
    return AgentRunDetail(**data, steps=steps, artifacts=artifacts)


@router.post("", response_model=AgentRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_agent(
    workspace_id: str,
    payload: AgentRunCreate,
    service: AgentService = Depends(_service),
) -> AgentRunRead:
    run = service.start(
        workspace_id,
        agent_type=payload.agent_type,
        prompt=payload.prompt,
        conversation_id=payload.conversation_id,
        input_payload=payload.input,
    )
    try:
        celery_id = spawn_agent_task(run.id)
        task = service.db.get(Task, run.task_id)
        if task:
            task.celery_task_id = celery_id
            service.db.commit()
    except Exception as exc:
        service.mark_dispatch_failed(run.id, str(exc))
        raise
    return AgentRunRead.model_validate(run)


@router.get("", response_model=AgentRunListResponse)
def list_agents(
    workspace_id: str,
    conversation_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: AgentService = Depends(_service),
) -> AgentRunListResponse:
    items, total = service.list(
        workspace_id,
        conversation_id=conversation_id,
        limit=limit,
        offset=offset,
    )
    return AgentRunListResponse(
        items=[AgentRunRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{run_id}", response_model=AgentRunDetail)
def get_agent(workspace_id: str, run_id: str, service: AgentService = Depends(_service)) -> AgentRunDetail:
    return _detail(service, workspace_id, run_id)


@router.post("/{run_id}/cancel", response_model=AgentRunRead)
def cancel_agent(workspace_id: str, run_id: str, service: AgentService = Depends(_service)) -> AgentRunRead:
    return AgentRunRead.model_validate(service.cancel(workspace_id, run_id))


@router.post("/{run_id}/confirm", response_model=AgentConfirmResponse)
def confirm_agent(workspace_id: str, run_id: str, service: AgentService = Depends(_service)) -> AgentConfirmResponse:
    run, plan = service.confirm(workspace_id, run_id)
    return AgentConfirmResponse(
        run=_detail(service, workspace_id, run.id),
        research_plan_id=plan.id if plan else None,
    )


@router.get("/{run_id}/artifacts/{artifact_id}")
def download_artifact(workspace_id: str, run_id: str, artifact_id: str, service: AgentService = Depends(_service)) -> Response:
    artifact = service.artifact(workspace_id, run_id, artifact_id)
    # RFC 5987: URL-encode the filename so non-ASCII names download correctly;
    # also expose a plain ASCII header the frontend can read without parsing
    # Content-Disposition quoting.
    filename = artifact.filename.split("/")[-1]
    return Response(
        content=artifact.content,
        media_type=artifact.mime_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"; '
                f"filename*=UTF-8''{quote(filename)}"
            ),
            "X-File-Name": quote(filename),
        },
    )


@router.get("/{run_id}/bundle")
def download_bundle(
    workspace_id: str,
    run_id: str,
    service: AgentService = Depends(_service),
    db: Session = Depends(get_db),
) -> Response:
    run, _, artifacts = service.detail(workspace_id, run_id)
    code = [item for item in artifacts if item.artifact_type == "code"]
    if not code:
        raise AgentInputError("该运行没有代码产物")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for artifact in code:
            archive.writestr(artifact.filename, artifact.content)
        archive.writestr(
            "ARTIFACT_STATUS.json",
            json.dumps(
                {
                    "format_version": "1",
                    "run_id": run.id,
                    "generated_by": "ai",
                    "run_status": run.status,
                    "artifacts": [
                        {
                            "filename": artifact.filename,
                            "artifact_type": artifact.artifact_type,
                            "validation_status": artifact.validation_status,
                            "created_at": artifact.created_at,
                        }
                        for artifact in artifacts
                    ],
                },
                ensure_ascii=False,
                default=str,
                indent=2,
            ),
        )
        # include the research plan the code was generated from (complete snapshot)
        plan_id = str((run.result or {}).get("research_plan_id") or "")
        plan = db.get(ResearchPlan, plan_id) if plan_id else None
        if plan and plan.workspace_id == workspace_id:
            snapshot = AgentService._plan_snapshot(plan)
            evidence = list((run.context_snapshot or {}).get("evidence") or [])
            archive.writestr("RESEARCH_PLAN.md", AgentService._plan_markdown(snapshot, evidence))
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="gapmind-agent-{run_id[:8]}.zip"'},
    )
