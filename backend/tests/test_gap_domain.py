from __future__ import annotations

import json
from uuid import uuid4
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.artifact.models import Artifact
from app.domains.artifact.service import ArtifactService
from app.domains.gap.markdown import compact_markdown
from app.domains.gap.models import PaperGapAnnotation
from app.domains.gap.normalization import canonical_axis_label
from app.domains.gap.schemas import GapCandidateDiscoverRequest
from app.domains.gap.service import GapService
from app.domains.gap.validation import validate_annotation
from app.domains.paper.models import Paper
from app.domains.task.schemas import TaskCreate
from app.domains.task.service import TaskService
from app.domains.timeline.models import TimelineEvent
from app.domains.workspace.models import Workspace
from app.gateway.gap_extractor import (
    GapExtractionResult,
    GapExtractorUnavailableError,
    OllamaGapExtractor,
    RemoteGapExtractor,
)
from app.domains.gap.validation import categorize_validation_errors, classify_failure_kind


def _output(
    *,
    problem_entity: str = "E2",
    problem_label: str = "解释稳定性不足",
    problem_type: str = "prior_work_gap",
    relation_type: str = "ADDRESSES",
    method_label: str = "子图扰动式解释",
    method_mechanism: str = "通过扰动定位关键子图",
) -> dict:
    return {
        "schema_version": "3.0",
        "paper": {
            "paper_name": "Paper",
            "authors": ["Author"],
            "research_domain": ["Graph Neural Networks"],
        },
        "entities": [
            {
                "entity_id": "E1",
                "name_original": "method",
                "name_normalized_zh": "方法",
                "type": "METHOD",
                "description_zh": "method",
            },
            {
                "entity_id": problem_entity,
                "name_original": "problem",
                "name_normalized_zh": problem_label,
                "type": "RESEARCH_PROBLEM",
                "description_zh": "problem",
            },
        ],
        "relations": [
            {
                "relation_id": "R1",
                "source_entity_id": "E1",
                "relation_type": relation_type,
                "target_entity_id": problem_entity,
            }
        ],
        "methods": [
            {
                "method_id": "M1",
                "corresponding_entity_id": "E1",
                "method_strategy_zh": method_label,
                "mechanism_zh": method_mechanism,
            }
        ],
        "problems": [
            {
                "problem_id": "P1",
                "corresponding_entity_id": problem_entity,
                "problem_label_zh": problem_label,
                "problem_type": problem_type,
                "description_zh": "问题说明",
            }
        ],
    }


def test_schema3_business_validation() -> None:
    parsed, errors = validate_annotation(_output())
    assert parsed is not None
    assert errors == []

    invalid = _output()
    invalid["entities"][1]["type"] = "DATASET"
    parsed, errors = validate_annotation(invalid)
    assert parsed is None
    assert any("DATASET" in error for error in errors)


def test_compact_markdown_drops_experiments_but_keeps_conclusion() -> None:
    source = """# Introduction
Core motivation.
# Experiments
Dataset and scores.
# Conclusion
The remaining limitation matters.
# Appendix
Extra tables.
"""

    compacted = compact_markdown(source)

    assert "Core motivation" in compacted
    assert "Dataset and scores" not in compacted
    assert "remaining limitation" in compacted
    assert "Extra tables" not in compacted


def test_audited_taxonomy_normalizes_only_supported_families() -> None:
    diffusion = canonical_axis_label(
        "method",
        "离散去噪扩散生成式解释",
        "生成图反事实样本",
    )
    vae = canonical_axis_label(
        "method",
        "图变分自编码器反事实生成",
        "在潜在空间生成反事实图",
    )
    problem = canonical_axis_label(
        "problem",
        "缺乏图神经网络的反事实解释方法",
        "现有研究对反事实生成探索不足",
    )

    assert diffusion is not None and diffusion.label == "生成式反事实解释"
    assert vae is not None and vae.label == "生成式反事实解释"
    assert problem is not None and problem.label == "反事实解释覆盖不足"
    assert max(len(diffusion.rule_id), len(vae.rule_id), len(problem.rule_id)) <= 32
    assert canonical_axis_label("method", "完全未知的新方法", "无受控词") is None


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _Client:
    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
        self.calls: list[dict] = []

    def post(self, url: str, *, json: dict) -> _Response:
        self.calls.append({"url": url, "json": json})
        return _Response({"message": {"content": self.contents.pop(0)}})


class _UnavailableClient:
    def post(self, url: str, *, json: dict) -> _Response:
        raise httpx.ConnectError("connection refused", request=httpx.Request("POST", url))


class _UnavailableExtractor:
    model_parameters = {"model": "unavailable"}

    def extract(self, markdown: str):
        raise GapExtractorUnavailableError("研究空白模型响应超时，请检查 SSH 隧道和服务器模型负载后重试。")


class _SuccessfulExtractor:
    model_parameters = {"provider": "local-test"}

    def extract(self, markdown: str):
        return GapExtractionResult(
            output=type("Output", (), {"model_dump": lambda self, mode: _output()})(),
            attempts=1,
            provider="ollama",
            model="local-test",
        )


class _FakeRemoteExtractor:
    provider = "remote"
    model = "remote-test"
    model_parameters = {"response_format": "json_object"}
    calls = 0

    def extract(self, markdown: str, *, repair_attempts: int | None = None):
        self.calls += 1
        return GapExtractionResult(
            output=type("Output", (), {"model_dump": lambda self, mode: _output()})(),
            attempts=1,
            provider=self.provider,
            model=self.model,
        )


class _FakeGateway:
    def __init__(self, content: str):
        self.content = content
        self.calls: list[dict] = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return type("Response", (), {"content": self.content})()


class _SequenceGateway:
    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
        self.calls: list[dict] = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return type("Response", (), {"content": self.contents.pop(0)})()


def test_ollama_extractor_repairs_invalid_enum() -> None:
    invalid = _output()
    invalid["entities"][1]["type"] = "DATASET"
    client = _Client([json.dumps(invalid), json.dumps(_output())])
    extractor = OllamaGapExtractor(
        base_url="http://ollama.test:11434", model="test-model", client=client
    )

    result = extractor.extract("# Introduction\nPaper", repair_attempts=1)

    assert result.output is not None
    assert result.attempts == 2
    assert len(client.calls) == 2
    assert "DATASET" in client.calls[1]["json"]["messages"][-1]["content"]


def test_ollama_extractor_reports_tunnel_failure_without_raw_transport_error() -> None:
    extractor = OllamaGapExtractor(
        base_url="http://ollama.test:11434",
        model="test-model",
        client=_UnavailableClient(),
    )

    with pytest.raises(GapExtractorUnavailableError) as exc_info:
        extractor.extract("# Introduction\nPaper")

    message = str(exc_info.value)
    assert "SSH 隧道" in message
    assert "connection refused" not in message


def test_gap_validation_failure_categories_and_content_boundaries() -> None:
    errors = [
        "model response does not contain a JSON object",
        "JSON Schema entities: Field required",
        "R1 的 ADDRESSES 必须是 METHOD → RESEARCH_PROBLEM。",
        "P1.problem_label_zh 与对应实体名称不一致。",
    ]
    assert categorize_validation_errors(errors) == [
        "json",
        "schema",
        "relation_direction",
        "label_consistency",
    ]
    assert classify_failure_kind("short markdown", errors) == "content_insufficient"
    assert classify_failure_kind(" ".join(["A research paper"] * 150), errors) == "invalid_output"
    assert classify_failure_kind(" ".join(["A survey article"] * 150), errors) == "not_applicable"


def test_remote_extractor_uses_structured_schema_and_disables_thinking() -> None:
    gateway = _FakeGateway(json.dumps(_output()))
    extractor = RemoteGapExtractor(
        api_key="test-key",
        base_url="https://remote.test/v1",
        model="remote-model",
        gateway=gateway,
    )

    result = extractor.extract("# Introduction\n" + "Paper body " * 100)

    assert result.output is not None
    assert result.provider == "remote"
    assert result.model == "remote-model"
    assert gateway.calls[0]["disable_thinking"] is True
    assert gateway.calls[0]["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in gateway.calls[0]


def test_remote_extractor_repairs_semantic_validation_failure() -> None:
    invalid = _output()
    invalid["entities"][1]["type"] = "MODEL"
    gateway = _SequenceGateway([json.dumps(invalid), json.dumps(_output())])
    extractor = RemoteGapExtractor(
        api_key="test-key",
        base_url="https://remote.test/v1",
        model="remote-model",
        gateway=gateway,
    )

    result = extractor.extract("# Introduction\n" + "Paper body " * 100, repair_attempts=1)

    assert result.output is not None
    assert result.attempts == 2
    assert len(result.raw_responses) == 2
    assert len(gateway.calls) == 2
    assert gateway.calls[1]["messages"][-2]["role"] == "assistant"
    assert "必须是 METHOD" in gateway.calls[1]["messages"][-1]["content"]


def test_gap_worker_marks_annotation_invalid_when_extractor_is_unavailable(
    db_session: Session, monkeypatch, tmp_path
) -> None:
    from app.workers.tasks import extract_gap_annotation as gap_task_mod

    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_enabled", False)

    workspace = Workspace(
        id=str(uuid4()),
        name="Gap unavailable",
        keywords=[],
        active_questions=[],
        is_archived=False,
        is_deleted=False,
    )
    artifact = Artifact(
        id=str(uuid4()),
        workspace_id=workspace.id,
        kind="parsed_markdown",
        file_path="unavailable.md",
        size_bytes=10,
        is_deleted=False,
    )
    paper = Paper(
        id=str(uuid4()),
        workspace_id=workspace.id,
        title="Unavailable model paper",
        authors=[],
        source="manual",
        parse_status="parsed",
        parsed_markdown_artifact_id=artifact.id,
        chunk_count=0,
        extract_status="not_applicable",
        is_deleted=False,
    )
    db_session.add_all([workspace, artifact, paper])
    db_session.commit()
    markdown_path = tmp_path / "unavailable.md"
    markdown_path.write_text("# Introduction\nPaper body", encoding="utf-8")
    monkeypatch.setattr(
        ArtifactService,
        "resolve_abs_path",
        lambda self, item: markdown_path,
    )
    task = TaskService(db_session).create(
        TaskCreate(
            workspace_id=workspace.id,
            task_type="extract_gap_annotation",
            payload={"paper_id": paper.id, "force": False},
        )
    )

    result = gap_task_mod._run_gap_extraction(
        db_session,
        task.id,
        extractor=_UnavailableExtractor(),
    )

    annotation = db_session.get(PaperGapAnnotation, result["annotation_id"])
    task_after = TaskService(db_session).get(task.id)
    # The worker payload keeps the annotation outcome; the authoritative Task
    # row records the failed state shown in the processing center.
    assert result["status"] == "unavailable"
    assert result["retryable"] is True
    assert annotation is not None and annotation.status == "invalid"
    assert annotation.validation_errors == [
        "研究空白模型响应超时，请检查 SSH 隧道和服务器模型负载后重试。"
    ]
    assert task_after.status == "failed"


def _prepare_gap_task(db_session: Session, monkeypatch, tmp_path) -> tuple[str, Paper]:
    from app.workers.tasks.extract_gap_annotation import _run_gap_extraction

    workspace = Workspace(
        id=str(uuid4()),
        name="Gap fallback",
        keywords=[],
        active_questions=[],
        is_archived=False,
        is_deleted=False,
    )
    artifact = Artifact(
        id=str(uuid4()),
        workspace_id=workspace.id,
        kind="parsed_markdown",
        file_path="fallback.md",
        size_bytes=10,
        is_deleted=False,
    )
    paper = Paper(
        id=str(uuid4()),
        workspace_id=workspace.id,
        title="Fallback paper",
        authors=[],
        source="manual",
        parse_status="parsed",
        parsed_markdown_artifact_id=artifact.id,
        chunk_count=0,
        extract_status="not_applicable",
        is_deleted=False,
    )
    db_session.add_all([workspace, artifact, paper])
    db_session.commit()
    markdown_path = tmp_path / "fallback.md"
    markdown_path.write_text("# Introduction\n" + "Research method and limitation. " * 100, encoding="utf-8")
    monkeypatch.setattr(ArtifactService, "resolve_abs_path", lambda self, item: markdown_path)
    task = TaskService(db_session).create(
        TaskCreate(
            workspace_id=workspace.id,
            task_type="extract_gap_annotation",
            payload={
                "paper_id": paper.id,
                "force": False,
            },
        )
    )
    return task.id, paper


def test_local_gap_extractor_remains_primary_when_remote_is_configured(
    db_session: Session, monkeypatch, tmp_path
) -> None:
    from app.workers.tasks import extract_gap_annotation as gap_task_mod

    task_id, _ = _prepare_gap_task(db_session, monkeypatch, tmp_path)
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_enabled", True)
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_base_url", "https://remote.test/v1")
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_api_key", "test-key")
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_model", "remote-model")
    monkeypatch.setattr(
        gap_task_mod,
        "RemoteGapExtractor",
        lambda: (_ for _ in ()).throw(AssertionError("remote fallback must not run after local success")),
    )

    result = gap_task_mod._run_gap_extraction(db_session, task_id, extractor=_SuccessfulExtractor())

    assert result["status"] == "valid"
    assert result["provider"] == "ollama"


def test_remote_gap_fallback_is_automatic_and_records_remote_provenance(
    db_session: Session, monkeypatch, tmp_path
) -> None:
    from app.workers.tasks import extract_gap_annotation as gap_task_mod

    task_id, paper = _prepare_gap_task(db_session, monkeypatch, tmp_path)
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_enabled", True)
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_base_url", "https://remote.test/v1")
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_api_key", "test-key")
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_model", "remote-model")
    remote = _FakeRemoteExtractor()
    monkeypatch.setattr(gap_task_mod, "RemoteGapExtractor", lambda: remote)

    result = gap_task_mod._run_gap_extraction(
        db_session,
        task_id,
        extractor=_UnavailableExtractor(),
    )

    assert result["status"] == "valid"
    assert result["remote_fallback"] is True
    assert result["provider"] == "remote"
    assert remote.calls == 1
    remote_row = db_session.get(PaperGapAnnotation, result["annotation_id"])
    assert remote_row is not None
    assert remote_row.paper_id == paper.id
    assert remote_row.model_provider == "remote"
    assert remote_row.fallback_reason == "local_model_unavailable"


def test_remote_gap_fallback_is_automatic_after_local_validation_failure(
    db_session: Session, monkeypatch, tmp_path
) -> None:
    from app.workers.tasks import extract_gap_annotation as gap_task_mod

    task_id, paper = _prepare_gap_task(db_session, monkeypatch, tmp_path)
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_enabled", True)
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_base_url", "https://remote.test/v1")
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_api_key", "test-key")
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_model", "remote-model")
    remote = _FakeRemoteExtractor()
    monkeypatch.setattr(gap_task_mod, "RemoteGapExtractor", lambda: remote)

    result = gap_task_mod._run_gap_extraction(db_session, task_id, extractor=_InvalidExtractor())

    assert result["status"] == "valid"
    assert result["remote_fallback"] is True
    assert result["provider"] == "remote"
    assert remote.calls == 1
    remote_row = db_session.get(PaperGapAnnotation, result["annotation_id"])
    assert remote_row is not None
    assert remote_row.paper_id == paper.id
    assert remote_row.fallback_reason == "local_validation_failed"


def test_remote_gap_fallback_does_not_send_when_remote_is_not_configured(
    db_session: Session, monkeypatch, tmp_path
) -> None:
    from app.workers.tasks import extract_gap_annotation as gap_task_mod

    task_id, _ = _prepare_gap_task(db_session, monkeypatch, tmp_path)
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_enabled", False)
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_base_url", "")
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_api_key", "")
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_model", "")
    remote = _FakeRemoteExtractor()
    monkeypatch.setattr(gap_task_mod, "RemoteGapExtractor", lambda: remote)

    result = gap_task_mod._run_gap_extraction(
        db_session,
        task_id,
        extractor=_UnavailableExtractor(),
    )

    assert result["status"] == "unavailable"
    assert result["fallback_reason"] == "remote_fallback_not_configured"
    assert remote.calls == 0


def test_gap_fallback_is_not_attempted_for_short_content(
    db_session: Session, monkeypatch, tmp_path
) -> None:
    from app.workers.tasks import extract_gap_annotation as gap_task_mod

    task_id, _ = _prepare_gap_task(db_session, monkeypatch, tmp_path)
    markdown_path = tmp_path / "fallback.md"
    markdown_path.write_text("short", encoding="utf-8")
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_enabled", True)
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_base_url", "https://remote.test/v1")
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_api_key", "test-key")
    monkeypatch.setattr(gap_task_mod.settings, "gap_extractor_remote_model", "remote-model")
    monkeypatch.setattr(
        gap_task_mod,
        "RemoteGapExtractor",
        lambda: (_ for _ in ()).throw(AssertionError("short content must not be sent remotely")),
    )

    result = gap_task_mod._run_gap_extraction(db_session, task_id, extractor=_InvalidExtractor())

    assert result["status"] == "invalid"
    assert "内容不足" in result["error"]
    assert TaskService(db_session).get(task_id).status == "failed"


class _InvalidExtractor:
    model_parameters = {"provider": "local-test"}

    def extract(self, markdown: str):
        return GapExtractionResult(
            output=None,
            attempts=3,
            validation_errors=["JSON Schema entities: Field required"],
            provider="ollama",
            model="local-test",
            validation_error_categories=["schema"],
        )


def _annotation(
    db: Session,
    workspace: Workspace,
    artifact: Artifact,
    paper: Paper,
    output: dict,
) -> PaperGapAnnotation:
    row = PaperGapAnnotation(
        id=str(uuid4()),
        workspace_id=workspace.id,
        paper_id=paper.id,
        artifact_id=artifact.id,
        task_id=None,
        input_sha256=uuid4().hex + uuid4().hex,
        schema_version="3.0",
        prompt_version="test",
        model_provider="ollama",
        model_name="test-model",
        model_digest=None,
        model_parameters={},
        status="valid",
        attempts=1,
        raw_responses=[],
        output=output,
        validation_errors=[],
        is_deleted=False,
    )
    db.add(row)
    db.commit()
    return row


def test_deterministic_board_marks_coverage_and_explicit_limitation(
    db_session: Session, client: TestClient
) -> None:
    workspace = Workspace(
        id=str(uuid4()),
        name="Gap WS",
        keywords=[],
        active_questions=[],
        is_archived=False,
        is_deleted=False,
    )
    db_session.add(workspace)
    db_session.flush()
    artifacts: list[Artifact] = []
    papers: list[Paper] = []
    for index in range(2):
        artifact = Artifact(
            id=str(uuid4()),
            workspace_id=workspace.id,
            kind="parsed_markdown",
            file_path=f"paper-{index}.md",
            size_bytes=10,
            is_deleted=False,
        )
        paper = Paper(
            id=str(uuid4()),
            workspace_id=workspace.id,
            title=f"Paper {index}",
            authors=[],
            source="manual",
            parse_status="parsed",
            chunk_count=0,
            extract_status="not_applicable",
            is_deleted=False,
        )
        db_session.add_all([artifact, paper])
        db_session.flush()
        papers.append(paper)
        artifacts.append(artifact)

    first = _annotation(db_session, workspace, artifacts[0], papers[0], _output())
    second_output = _output(
        problem_label="跨数据集泛化不足",
        problem_type="residual_limitation",
        relation_type="HAS_LIMITATION",
    )
    second = _annotation(db_session, workspace, artifacts[1], papers[1], second_output)

    service = GapService(db_session)
    service.assign_annotation(first)
    service.assign_annotation(second)
    board = service.rebuild_board(workspace.id)

    assert len(board.method_axes) == 1
    assert len(board.problem_axes) == 2
    assert len(board.cells) == 2
    covered = next(item for item in board.cells if item["addressed"])
    candidate = next(item for item in board.cells if not item["addressed"])
    assert covered["addressed_paper_ids"] == [papers[0].id]
    assert candidate["explicit_limitation"] is True
    assert candidate["eligible_for_discovery"] is True
    assert candidate["candidate_tier"] == "explicit_limitation"
    assert candidate["limitation_paper_ids"] == [papers[1].id]
    assert board.candidate_count == 1

    response = client.get(f"/api/v1/workspaces/{workspace.id}/gap/board")
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1


def test_board_collapses_taxonomy_and_suppresses_cartesian_only_cells(
    db_session: Session,
    client: TestClient,
) -> None:
    actor_id = str(uuid4())
    workspace = Workspace(
        id=str(uuid4()),
        name="Taxonomy WS",
        owner_id=actor_id,
        keywords=[],
        active_questions=[],
        is_archived=False,
        is_deleted=False,
    )
    db_session.add(workspace)
    db_session.flush()
    outputs = [
        _output(
            method_label="离散去噪扩散生成式解释",
            method_mechanism="生成图反事实样本",
            problem_label="缺乏图神经网络的反事实解释方法",
        ),
        _output(
            method_label="图变分自编码器反事实生成",
            method_mechanism="在潜在空间生成反事实图",
            problem_label="反事实与模型级解释探索不足",
        ),
        _output(
            method_label="因果结构建模与神经因果推断解释",
            method_mechanism="通过因果推断生成解释",
            problem_label="缺乏真实标签时GNN解释评估困难",
        ),
    ]
    for index, output in enumerate(outputs):
        artifact = Artifact(
            id=str(uuid4()),
            workspace_id=workspace.id,
            kind="parsed_markdown",
            file_path=f"taxonomy-{index}.md",
            size_bytes=10,
            is_deleted=False,
        )
        paper = Paper(
            id=str(uuid4()),
            workspace_id=workspace.id,
            title=f"Taxonomy Paper {index}",
            authors=[],
            source="manual",
            parse_status="parsed",
            chunk_count=0,
            extract_status="not_applicable",
            is_deleted=False,
        )
        db_session.add_all([artifact, paper])
        db_session.flush()
        _annotation(db_session, workspace, artifact, paper, output)

    board = GapService(db_session).rebuild_board(workspace.id)

    assert len(board.method_axes) == 2
    assert len(board.problem_axes) == 2
    assert (
        next(item for item in board.method_axes if item["label"] == "生成式反事实解释")[
            "paper_count"
        ]
        == 2
    )
    assert (
        next(item for item in board.problem_axes if item["label"] == "反事实解释覆盖不足")[
            "paper_count"
        ]
        == 2
    )
    low_evidence = [
        item for item in board.cells if not item["addressed"] and not item["eligible_for_discovery"]
    ]
    assert len(low_evidence) == 2
    assert {item["candidate_tier"] for item in low_evidence} == {"corpus_only"}
    assert board.candidate_count == 0

    rejected = client.post(
        f"/api/v1/workspaces/{workspace.id}/gap/candidates/discover",
        json={
            "method_concept_id": low_evidence[0]["method_concept_id"],
            "problem_concept_id": low_evidence[0]["problem_concept_id"],
            "max_opportunities": 3,
        },
        headers={"X-User-ID": actor_id},
    )
    assert rejected.status_code == 409
    assert "low-evidence" in rejected.json()["detail"]

    with patch(
        "app.domains.gap.router.spawn_discover_task",
        return_value="celery-gap-discover-test",
    ):
        launched = client.post(
            f"/api/v1/workspaces/{workspace.id}/gap/candidates/discover",
            json={
                "method_concept_id": low_evidence[0]["method_concept_id"],
                "problem_concept_id": low_evidence[0]["problem_concept_id"],
                "max_opportunities": 3,
                "exploratory": True,
            },
            headers={"X-User-ID": actor_id},
        )
    assert launched.status_code == 202, launched.text
    timeline_event = db_session.scalar(
        select(TimelineEvent).where(
            TimelineEvent.workspace_id == workspace.id,
            TimelineEvent.event_type == "discover.run_created",
        )
    )
    assert timeline_event is not None
    assert timeline_event.actor == actor_id

    exploratory = GapCandidateDiscoverRequest(
        method_concept_id=low_evidence[0]["method_concept_id"],
        problem_concept_id=low_evidence[0]["problem_concept_id"],
        exploratory=True,
    )
    assert exploratory.exploratory is True


def test_spawn_gap_extraction_skips_already_annotated_paper(db_session: Session) -> None:
    """spawn_gap_extraction must NOT enqueue a task for a paper that already has
    a valid annotation (so "抽取已解析论文" on a large corpus only processes
    genuinely new papers)."""
    from app.core.config import settings
    from app.workers.tasks.extract_gap_annotation import (
        PROMPT_VERSION,
        spawn_gap_extraction,
    )

    workspace = Workspace(
        id=str(uuid4()),
        name="Gap Skip",
        keywords=[],
        active_questions=[],
        is_archived=False,
        is_deleted=False,
    )
    db_session.add(workspace)
    db_session.flush()
    artifact = Artifact(
        id=str(uuid4()),
        workspace_id=workspace.id,
        kind="parsed_markdown",
        file_path="p.md",
        size_bytes=10,
        is_deleted=False,
    )
    paper = Paper(
        id=str(uuid4()),
        workspace_id=workspace.id,
        title="P",
        authors=[],
        source="manual",
        parse_status="parsed",
        parsed_markdown_artifact_id=artifact.id,
        chunk_count=0,
        extract_status="not_applicable",
        is_deleted=False,
    )
    db_session.add_all([artifact, paper])
    db_session.flush()

    row = _annotation(db_session, workspace, artifact, paper, _output())
    row.model_name = settings.gap_extractor_model
    row.prompt_version = PROMPT_VERSION
    db_session.commit()

    task_id, skipped = spawn_gap_extraction(db_session, paper.id, workspace.id)
    assert skipped is True
    assert task_id is None


def test_spawn_gap_extraction_skips_valid_remote_or_old_prompt_annotation(
    db_session: Session,
) -> None:
    """Incremental extraction must reuse valid results across provider/version."""
    from app.workers.tasks.extract_gap_annotation import spawn_gap_extraction

    workspace = Workspace(
        id=str(uuid4()),
        name="Gap Skip Existing",
        keywords=[],
        active_questions=[],
        is_archived=False,
        is_deleted=False,
    )
    db_session.add(workspace)
    db_session.flush()
    artifact = Artifact(
        id=str(uuid4()),
        workspace_id=workspace.id,
        kind="parsed_markdown",
        file_path="p.md",
        size_bytes=10,
        is_deleted=False,
    )
    paper = Paper(
        id=str(uuid4()),
        workspace_id=workspace.id,
        title="P",
        authors=[],
        source="manual",
        parse_status="parsed",
        parsed_markdown_artifact_id=artifact.id,
        chunk_count=0,
        extract_status="not_applicable",
        is_deleted=False,
    )
    db_session.add_all([artifact, paper])
    db_session.flush()

    row = _annotation(db_session, workspace, artifact, paper, _output())
    row.model_name = "remote-model"
    row.model_provider = "remote"
    row.prompt_version = "gap-schema3-v1"
    db_session.commit()

    task_id, skipped = spawn_gap_extraction(db_session, paper.id, workspace.id)

    assert skipped is True
    assert task_id is None


def test_run_gap_extraction_skips_queued_duplicate_for_valid_annotation(
    db_session: Session, monkeypatch, tmp_path
) -> None:
    """Already queued duplicate tasks must not call a model after the fix."""
    from app.workers.tasks import extract_gap_annotation as gap_task_mod

    task_id, paper = _prepare_gap_task(db_session, monkeypatch, tmp_path)
    workspace = db_session.get(Workspace, paper.workspace_id)
    artifact = db_session.get(Artifact, paper.parsed_markdown_artifact_id)
    assert workspace is not None and artifact is not None
    existing = _annotation(db_session, workspace, artifact, paper, _output())

    class _MustNotRunExtractor:
        def extract(self, markdown: str):
            raise AssertionError("queued duplicate must be skipped")

    result = gap_task_mod._run_gap_extraction(
        db_session,
        task_id,
        extractor=_MustNotRunExtractor(),
    )

    assert result == {
        "annotation_id": existing.id,
        "status": "valid",
        "provider": existing.model_provider,
        "idempotent": True,
    }
    assert TaskService(db_session).get(task_id).status == "succeeded"


def test_spawn_gap_extraction_does_not_skip_without_annotation(db_session: Session, monkeypatch) -> None:
    """Without a valid annotation, spawn_gap_extraction enqueues a task."""
    from app.workers.tasks.extract_gap_annotation import spawn_gap_extraction

    workspace = Workspace(
        id=str(uuid4()),
        name="Gap NoSkip",
        keywords=[],
        active_questions=[],
        is_archived=False,
        is_deleted=False,
    )
    db_session.add(workspace)
    db_session.flush()
    artifact = Artifact(
        id=str(uuid4()),
        workspace_id=workspace.id,
        kind="parsed_markdown",
        file_path="p.md",
        size_bytes=10,
        is_deleted=False,
    )
    paper = Paper(
        id=str(uuid4()),
        workspace_id=workspace.id,
        title="P",
        authors=[],
        source="manual",
        parse_status="parsed",
        parsed_markdown_artifact_id=artifact.id,
        chunk_count=0,
        extract_status="not_applicable",
        is_deleted=False,
    )
    db_session.add_all([artifact, paper])
    db_session.flush()

    import app.workers.tasks.extract_gap_annotation as gap_task_mod

    monkeypatch.setattr(gap_task_mod.extract_gap_annotation_task, "delay", lambda tid: type("R", (), {"id": "x"})())
    task_id, skipped = spawn_gap_extraction(db_session, paper.id, workspace.id)
    assert skipped is False
    assert task_id
