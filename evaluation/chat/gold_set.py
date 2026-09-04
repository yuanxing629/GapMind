"""离线 Workspace Chat QA benchmark 的 schemas。

检索门禁回答的是一篇论文能否被召回。本 benchmark 再向后推进一层：给定已保存的 Chat 响应，
它是否使用了真实论文标记、保持计划/报告/代码来源相互区分，并达到人工标注的可回答性结论？
它刻意不执行 LLM 或修改工作区，因此评测数据仍然可复核、可复现。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from evaluation.retrieval.gold_set import Freeze


ExpectedVerdict = Literal["supported", "insufficient_evidence"]
HumanVerdict = Literal["supported", "insufficient_evidence", "unsupported"]
RetrievalAuditStatus = Literal["succeeded", "degraded", "failed", "unknown"]
RerankerAuditStatus = Literal[
    "applied",
    "enabled_no_rerank",
    "degraded",
    "disabled",
    "unknown",
]


class ChatContext(BaseModel):
    """重放问题前所需的显式非论文上下文。"""

    mode: Literal["workspace_papers", "workspace_with_confirmed_plan"] = "workspace_papers"
    research_plan_ref: str | None = Field(
        default=None,
        description="Human-readable confirmed plan title or local id; never a paper reference.",
    )

    @model_validator(mode="after")
    def _plan_context_requires_ref(self) -> "ChatContext":
        if self.mode == "workspace_with_confirmed_plan" and not self.research_plan_ref:
            raise ValueError("workspace_with_confirmed_plan requires research_plan_ref")
        return self


class ChatQAQuestion(BaseModel):
    """一个人工标注问题及其预期证据契约。"""

    query_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=3, max_length=2000)
    expected_verdict: ExpectedVerdict
    required_paper_refs: list[str] = Field(default_factory=list, max_length=10)
    context: ChatContext = Field(default_factory=ChatContext)
    note: str | None = None

    @model_validator(mode="after")
    def _validate_evidence_contract(self) -> "ChatQAQuestion":
        refs = [ref.strip() for ref in self.required_paper_refs if ref.strip()]
        if len(refs) != len(set(ref.casefold() for ref in refs)):
            raise ValueError("required_paper_refs must not contain duplicates")
        if self.expected_verdict == "supported" and not refs:
            raise ValueError("supported questions require at least one required_paper_ref")
        if self.expected_verdict == "insufficient_evidence" and refs:
            raise ValueError("insufficient_evidence questions must not declare required_paper_refs")
        self.required_paper_refs = refs
        return self


class ChatQAGoldSet(BaseModel):
    """一个 workspace 语料的冻结人工预期。"""

    schema_version: str = "1.0.0"
    case_id: str = Field(min_length=1, max_length=128)
    corpus_version: str = Field(min_length=1, max_length=255)
    annotation_status: Literal["draft", "gold"] = "draft"
    freeze: Freeze = Field(default_factory=Freeze)
    workspace_hint: str | None = None
    questions: list[ChatQAQuestion] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _unique_query_ids(self) -> "ChatQAGoldSet":
        query_ids = [question.query_id for question in self.questions]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("questions must use unique query_id values")
        return self


class EvidenceSnapshot(BaseModel):
    """校验一个 ``[En]`` 标记所需的持久化论文证据。"""

    rank: int = Field(ge=1)
    paper_ref: str = Field(min_length=1)


class SourceSnapshot(BaseModel):
    """从 ChatMessage.source_manifest 复制的非论文来源。"""

    marker: str = Field(pattern=r"^[PDC][1-9][0-9]*$")
    source_type: Literal["plan", "report", "code_draft"]
    title: str = Field(min_length=1)


class RetrievalAuditSnapshot(BaseModel):
    """一次检索运行的匿名、非权威快照。

    有意省略 request id：QA 快照用于衡量检索覆盖度和延迟，而不是追踪本地数据库行。
    这些字段永远不决定回答是否有事实支持。
    """

    status: RetrievalAuditStatus = "unknown"
    diagnostic_code: str | None = None
    recall_count: int | None = Field(default=None, ge=0)
    returned_chunk_count: int = Field(default=0, ge=0)
    final_paper_count: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0)
    reranker_status: RerankerAuditStatus = "unknown"


class ChatQAObservation(BaseModel):
    """为离线 QA 评测导出的一条已保存 Chat 回答。"""

    query_id: str = Field(min_length=1, max_length=128)
    message_id: str | None = None
    answer_text: str = Field(min_length=1)
    grounding_status: str = Field(min_length=1)
    evidence: list[EvidenceSnapshot] = Field(default_factory=list, max_length=20)
    sources: list[SourceSnapshot] = Field(default_factory=list, max_length=10)
    retrieval_audit: RetrievalAuditSnapshot | None = None
    human_verdict: HumanVerdict | None = None

    @model_validator(mode="after")
    def _unique_snapshot_markers(self) -> "ChatQAObservation":
        evidence_ranks = [item.rank for item in self.evidence]
        source_markers = [item.marker for item in self.sources]
        if len(evidence_ranks) != len(set(evidence_ranks)):
            raise ValueError("evidence ranks must be unique")
        if len(source_markers) != len(set(source_markers)):
            raise ValueError("source markers must be unique")
        return self


class ChatQAObservationSet(BaseModel):
    """恰好对应一个 Chat QA gold case 的观测回答。"""

    gold_case_id: str = Field(min_length=1, max_length=128)
    observations: list[ChatQAObservation] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _unique_query_ids(self) -> "ChatQAObservationSet":
        query_ids = [item.query_id for item in self.observations]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("observations must use unique query_id values")
        return self


__all__ = [
    "ChatContext",
    "ChatQAGoldSet",
    "ChatQAObservation",
    "ChatQAObservationSet",
    "ChatQAQuestion",
    "EvidenceSnapshot",
    "HumanVerdict",
    "RetrievalAuditSnapshot",
    "RetrievalAuditStatus",
    "RerankerAuditStatus",
    "SourceSnapshot",
]
