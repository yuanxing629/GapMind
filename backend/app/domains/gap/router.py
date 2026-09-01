"""HTTP API for specialized extraction, board projection, and Discover handoff."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, get_owned_workspace
from app.domains.discover.schemas import (
    DiscoverConfig,
    DiscoverInput,
    DiscoverRunCreateRequest,
)
from app.domains.discover.service import DiscoverService
from app.domains.gap.schemas import (
    GapAnnotationListResponse,
    GapAnnotationRead,
    GapBoardRead,
    GapBoardRebuildRequest,
    GapCandidateDiscoverRequest,
    GapCandidateDiscoverResponse,
    GapExtractionRequest,
    GapExtractionResponse,
    GapExtractionTask,
)
from app.domains.gap.service import (
    GapBoardNotFoundError,
    GapCellNotFoundError,
    GapService,
)
from app.domains.task.service import TaskService
from app.workers.tasks.extract_gap_annotation import spawn_gap_extraction
from app.workers.tasks.run_discover import spawn_discover_task

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[str, Depends(get_current_user)]


def _service(db: DbSession) -> GapService:
    return GapService(db)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/gap",
    tags=["gap-board"],
    dependencies=[Depends(get_owned_workspace)],
)


@router.post(
    "/extractions",
    response_model=GapExtractionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def extract_papers(
    workspace_id: str,
    payload: GapExtractionRequest,
    db: DbSession,
) -> GapExtractionResponse:
    tasks: list[GapExtractionTask] = []
    for paper_id in dict.fromkeys(payload.paper_ids):
        try:
            task_id, skipped = spawn_gap_extraction(
                db,
                paper_id,
                workspace_id,
                force=payload.force,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if skipped:
            tasks.append(GapExtractionTask(paper_id=paper_id, task_id="", status="succeeded", skipped=True))
            continue
        task = TaskService(db).get(task_id)
        tasks.append(GapExtractionTask(paper_id=paper_id, task_id=task_id, status=task.status))
    return GapExtractionResponse(tasks=tasks)


@router.get("/annotations", response_model=GapAnnotationListResponse)
def list_annotations(
    workspace_id: str,
    service: Annotated[GapService, Depends(_service)],
    status_filter: str | None = Query(None, alias="status"),
) -> GapAnnotationListResponse:
    items = service.list_annotations(workspace_id, status=status_filter)
    return GapAnnotationListResponse(
        items=[GapAnnotationRead.model_validate(item) for item in items],
        total=len(items),
    )


@router.post("/board/rebuild", response_model=GapBoardRead)
def rebuild_board(
    workspace_id: str,
    payload: GapBoardRebuildRequest,
    service: Annotated[GapService, Depends(_service)],
) -> GapBoardRead:
    return GapBoardRead.model_validate(
        service.rebuild_board(workspace_id, paper_ids=payload.paper_ids)
    )


@router.get("/board", response_model=GapBoardRead)
def get_board(
    workspace_id: str,
    service: Annotated[GapService, Depends(_service)],
) -> GapBoardRead:
    try:
        return GapBoardRead.model_validate(service.latest_board(workspace_id))
    except GapBoardNotFoundError as exc:
        raise HTTPException(status_code=404, detail="gap board has not been built") from exc


@router.post(
    "/candidates/discover",
    response_model=GapCandidateDiscoverResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def discover_candidate(
    workspace_id: str,
    payload: GapCandidateDiscoverRequest,
    service: Annotated[GapService, Depends(_service)],
    db: DbSession,
    current_user: CurrentUser,
) -> GapCandidateDiscoverResponse:
    try:
        context = service.candidate_context(
            workspace_id, payload.method_concept_id, payload.problem_concept_id
        )
    except (GapBoardNotFoundError, GapCellNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="gap board cell not found") from exc
    cell = context["cell"]
    if cell["addressed"]:
        raise HTTPException(
            status_code=409,
            detail="the selected method-problem pair is already covered",
        )
    exploratory = bool(payload.exploratory)
    if not cell.get("eligible_for_discovery", False) and not exploratory:
        raise HTTPException(
            status_code=409,
            detail=(
                "the selected cell is only a low-evidence corpus absence; "
                "set exploratory=true to verify it as a speculative transfer hypothesis, "
                "or select a recommended candidate"
            ),
        )
    method_label = context["method"]["label"]
    problem_label = context["problem"]["label"]
    limitation_note = (
        f"该组合有 {len(cell['limitation_paper_ids'])} 篇论文提供显式剩余局限信号。"
        if cell["explicit_limitation"]
        else (
            "该组合来自方法族与问题族的跨论文迁移信号，需要核验二者的机制兼容性。"
            if cell.get("eligible_for_discovery", False)
            else "该组合仅由棋盘横纵轴笛卡尔积产生，当前没有直接关联证据。"
        )
    )
    topic_prefix = "探索性核验潜在方法迁移" if exploratory else "核验候选研究空白"
    topic = (
        f"{topic_prefix}：是否可以使用‘{method_label}’解决‘{problem_label}’？"
        f"{limitation_note} 必须检索相似工作、外部论文和反证后再判断。"
    )
    exploratory_constraint = (
        "这是低证据探索性假设。必须先验证方法机制与问题成因是否兼容，再核验是否已有相似工作；"
        "若缺乏支持证据，只能输出 needs_more_evidence，不得包装成已确认研究空白。"
        if exploratory
        else None
    )
    constraints = "\n".join(
        part
        for part in [
            payload.constraints,
            "棋盘空格只表示当前语料未发现 ADDRESSES，不得直接宣称学术界无人研究。",
            exploratory_constraint,
        ]
        if part
    )
    request = DiscoverRunCreateRequest(
        input=DiscoverInput(
            topic=topic,
            paper_ids=list(
                dict.fromkeys(
                    [
                        *context["method"].get("paper_ids", []),
                        *context["problem"].get("paper_ids", []),
                        *cell["limitation_paper_ids"],
                    ]
                )
            ),
            keywords=[method_label, problem_label],
            constraints=constraints,
        ),
        config=DiscoverConfig(max_opportunities=payload.max_opportunities),
    )
    run, task_id = DiscoverService(db).create_run(workspace_id, request, actor=current_user)
    celery_id = spawn_discover_task(run.id)
    task = TaskService(db).get(task_id)
    task.celery_task_id = celery_id
    db.commit()
    return GapCandidateDiscoverResponse(run_id=run.id, task_id=task_id, status=run.status)
