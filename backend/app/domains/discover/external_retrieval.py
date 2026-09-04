"""ExternalRetrievalService：Semantic Scholar 外部新颖性搜索与 OA 导入。

该服务从单体 DiscoverService 中拆出（MA-1 维护性重构）。
它负责外部新颖性阶段所有与外部世界交互的工作：查询构建、S2 相关性/精确查找、角色判断、
OA PDF 导入，以及在导入论文准备好后恢复运行的全文流水线状态机。

它持有 ``service`` 反向引用（DiscoverService 门面），使 ``_cancelled`` / ``_claim_text`` /
``_parse_json`` 等共享辅助函数集中在一个位置，而不是重复实现。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any
from uuid import uuid4
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domains.artifact.models import Artifact
from app.domains.artifact.service import ArtifactService
from app.domains.discover.models import DiscoverExternalCandidate, DiscoverRun
from app.domains.discover.ports import ExternalSearchPort
from app.domains.discover.schemas import DiscoverConfig, DiscoverScope
from app.domains.discover.utils import accumulate_tokens, parse_json
from app.domains.knowledge.models import EvidenceSpan, KnowledgeItem
from app.domains.paper.models import Paper
from app.domains.paper.schemas import PaperCreate
from app.domains.paper.service import PaperService
from app.domains.task.models import Task
from app.domains.task.service import TaskService
from app.gateway.llm import LLMGateway
from app.gateway.semantic_scholar import (
    SemanticScholarClient,
    SemanticScholarError,
    semantic_scholar_failure_kind,
)

logger = get_logger(__name__)

S2_FIELDS = "paperId,externalIds,title,abstract,year,authors,openAccessPdf,url,publicationDate,citationCount"
PIPELINE_PENDING_STATUSES = {"queued", "running", "waiting_for_user"}
RETRYABLE_EXTERNAL_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# 外部候选角色判断的 LLM prompt（Stage 3）。
EXTERNAL_ROLE_SYSTEM_PROMPT = """\
You classify whether external research papers serve as counter-evidence for a \
research question.

Categories:
- similar: same research area, closely related approach
- overlap: partially overlapping topic but different focus
- qualifies: adds caveats or limitations that constrain the research question
- contradicts: provides evidence against the research question
- unknown: cannot determine from the metadata alone

Rules:
- Be conservative: use "unknown" if ambiguous
- "contradicts" requires clear opposing evidence, not just a different focus
- Base your judgement on the title and abstract only
- A candidate that merely resembles the question is "similar"; only call it \
"qualifies" or "contradicts" when it explicitly challenges or constrains it

Output a JSON object, nothing else:
{"roles": [{"index": 0, "role": "similar|overlap|qualifies|contradicts|unknown", \
"confidence": 0.0-1.0}, ...]}"""

# 外部 query 研究轴拆解的 LLM prompt（Stage 3）。单独使用研究问题的长段文字，
# 作为 Semantic Scholar relevance query 的效果较差；LLM 会将其拆解为简洁且术语丰富的
# 搜索 query，分别覆盖基础方法、重叠工作、反证和评估/批评文献，并以 workspace 提取的
# method/limitation 为上下文，使 query 能够跨越研究轴并覆盖 workspace 中命名的方法。
EXTERNAL_QUERY_AXIS_SYSTEM_PROMPT = 'You write effective search queries to find EXTERNAL papers that challenge, overlap with, or foundationally support a research question. The papers must be relevant to the question but are NOT required to be in the user\'s workspace.\n\nRules:\n- Write CONCISE keyword-style queries (3-8 words), never full sentences\n- Prefer SPECIFIC method names and established concept terms over generic topic phrases (e.g. "graph information bottleneck" or "invariant risk minimization", not just "interpretable GNN")\n- Turn at least 2 of the workspace\'s abbreviated method names into concrete queries using their FULL names, so the search finds the method\'s paper plus its variants and critiques\n- Cover distinct angles: foundational methods, overlapping prior work, counter-evidence / critiques, evaluation benchmarks, and the domain axis (e.g. distribution shift) when present\n- Do not quote the workspace paper titles verbatim\n- Never repeat the same idea in two queries\n\nAlso choose up to 4 workspace method names whose papers you want surfaced PRECISELY (these are searched by exact title, so give the full descriptive name — expand abbreviations). Prefer methods that are foundational or likely to have counter-evidence / variants. Do not list the same method twice.\n\nExamples of good queries:\n- "graph information bottleneck"\n- "invariant risk minimization out-of-distribution"\n- "saliency maps sanity checks"\n- "explanation robustness adversarial perturbations"\n- "graph rationalization environment augmentation"\n\nOutput a JSON object, nothing else:\n{"queries": ["...", "...", "..."], "exact_lookups": ["Method Full Name", "...", "..."]}'

# 即使某些部署仍加载这个精简的 legacy prompt literal，也要保持持久化 prompt 文本
# 与强制执行的 lookup budget 一致。
EXTERNAL_QUERY_AXIS_SYSTEM_PROMPT = EXTERNAL_QUERY_AXIS_SYSTEM_PROMPT.replace(
    "up to 4 workspace method names", "up to 2 workspace method names"
)

EXTERNAL_FULLTEXT_ROLE_SYSTEM_PROMPT = """\
You classify whether an external research paper serves as counter-evidence for \
a research question, based on its FULL TEXT.

Categories:
- similar: same research area, closely related approach
- overlap: partially overlapping topic but different focus
- qualifies: adds caveats or limitations that constrain the research question
- contradicts: provides evidence against the research question
- unknown: cannot determine from the text

Rules:
- Be conservative: use "unknown" if ambiguous
- "contradicts" requires clear opposing evidence, not just a different focus
- Base your judgement on the FULL TEXT (not just the abstract) — e.g. an
  experiment that directly challenges the question's core assumption
- A paper that merely resembles the question is "similar"; only call it
  "qualifies" or "contradicts" when it explicitly challenges or constrains it

Output a JSON object, nothing else:
{"role": "similar|overlap|qualifies|contradicts|unknown", \
"confidence": 0.0-1.0}"""

# Stage 3 外部 query 构建预算。少量聚焦 query 比直接使用 claim 原文能覆盖更多角度，
# 同时保持 S2 API 调用和 LLM 角色判断 batch 有界。LLM 拆解出的研究轴 query 是价值最高
# 的外部搜索 key，因此优先级高于原始 workspace 信号和通用关键词。
# 默认 request budget 保持低于 provider 对 API key 的初始速率建议，同时保留 primary、
# counter、evaluation 三类专用研究轴。成功响应会被缓存，重试 run 不会重复缓存工作。
EXTERNAL_QUERY_MAX_TOTAL = 8  # 每个 run 的外部搜索 query 最大数
EXTERNAL_QUERY_AXIS_COUNT = 5  # 请求的 LLM 生成研究轴 query 数
EXTERNAL_QUERY_MAX_EXACT_LOOKUPS = 2  # 通过 exact title lookup 的 LLM 选定方法名称数
EXTERNAL_QUERY_SIGNAL_TYPES = ("method", "claim", "task", "limitation")
EXTERNAL_QUERY_MIN_CONFIDENCE = 0.3  # 跳过低置信度的抽取信号
EXTERNAL_QUERY_MAX_KEYWORDS = 2  # 通用用户关键词的优先级最低
EXTERNAL_QUERY_MIN_SUCCESS_RATE = 0.8
EXTERNAL_MIN_CANDIDATES_FOR_CLEAN_STATUS = 2
EXTERNAL_COUNTER_TOKENS = {
    "counter", "critique", "failure", "limitation", "robustness", "stability",
    "sanity", "challenge", "adversarial", "caveat", "反证", "限制", "稳定性",
}
EXTERNAL_EVALUATION_TOKENS = {
    "evaluation", "benchmark", "metric", "metrics", "test", "testing",
    "assess", "assessment", "实验", "评估", "指标",
}
# 用于合并各 query 结果列表的 reciprocal-rank-fusion 常量（P2-4）。
RRF_K = 60  # 标准 RRF 平滑参数：减弱不同 query 结果位置的差异
# Architecture component 不是命名的研究贡献，因此不适合作为外部搜索 key；
# 降低其优先级，让真正的方法名称优先。
EXTERNAL_METHOD_COMPONENT_TOKENS = {
    "pool", "module", "layer", "encoder", "decoder", "aggregation",
    "step", "fourier", "regularization", "block",
}


# --------------------------------------------------------------------- 辅助函数


def normalize_pdf_url(url: str) -> str:
    """规范化开放获取 PDF 下载地址（W1）。

    Semantic Scholar 偶尔返回 `http://` 或相对协议（`//host/...`）地址，
    arXiv 的 `abs` 页面是 HTML 而不是 PDF。``download_pdf`` 要求 HTTPS，
    因此在这里规范化为可获取的绝对 ``https://`` 地址；不可获取的协议最终进入 ``no_pdf``。
    """
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    elif url.lower().startswith("http://"):
        url = "https://" + url[len("http://"):]
    if url.startswith("https://arxiv.org/abs/"):
        url = url.replace("https://arxiv.org/abs/", "https://arxiv.org/pdf/")
    return url


def external_role(query: str, item: dict[str, Any]) -> str:
    """低成本的词重叠启发式角色判断；随后由 _judge_external_roles 使用 LLM 优化。"""
    haystack = f"{item.get('title', '')} {item.get('abstract', '')}".lower()
    tokens = [token for token in re.findall(r"[a-z0-9]{4,}", query.lower()) if token not in {"with", "from", "under", "using"}]
    overlap = sum(token in haystack for token in tokens)
    return "similar" if overlap >= max(1, len(tokens) // 4) else "unknown"


def title_verified(name: str, title: str) -> bool:
    """当查询词出现在标题中时，接受精确标题查找命中。"""
    query_words = set(re.findall(r"[a-z0-9]+", name.lower()))
    title_words = set(re.findall(r"[a-z0-9]+", title.lower()))
    if len(query_words) < 2:
        return False
    return query_words.issubset(title_words)


# --------------------------------------------------------------------- 服务


class ExternalRetrievalService:
    """外部新颖性搜索与 OA 全文导入编排。

    由 ``DiscoverService`` 组合使用；调用方应通过该门面访问，以保持使用
    ``service._external_*`` 的既有测试继续有效。
    """

    def __init__(
        self,
        db: Session,
        llm: LLMGateway,
        external_search: ExternalSearchPort,
        *,
        service=None,
    ) -> None:
        self.db = db
        self.llm = llm
        self.external_search = external_search
        self.service = service  # DiscoverService facade for shared helpers

# ---------------------------------------------------------- pipeline 状态
    def _paper_pipeline_state(self, paper_id: str) -> dict[str, Any]:
        paper = self.db.get(Paper, paper_id)
        if paper is None or paper.is_deleted:
            return {"ready": False, "failed": True, "error": "Imported paper was deleted or not found."}
        if paper.parse_status in {"pending", "parsing"} or not paper.parsed_markdown_artifact_id:
            return {"ready": False, "failed": False, "error": "PDF parsing is still running."}
        if paper.parse_status == "failed":
            return {"ready": False, "failed": True, "error": "PDF parsing failed."}
        if paper.extract_status in {"pending", "extracting", "not_applicable"}:
            return {"ready": False, "failed": False, "error": "Knowledge extraction is still running."}
        if paper.extract_status == "failed":
            return {"ready": False, "failed": True, "error": "Knowledge extraction failed."}
        span_count = int(
            self.db.execute(
                select(func.count()).select_from(EvidenceSpan).where(
                    EvidenceSpan.workspace_id == paper.workspace_id,
                    EvidenceSpan.paper_id == paper.id,
                    EvidenceSpan.is_deleted.is_(False),
                )
            ).scalar()
            or 0
        )
        if span_count == 0:
            return {"ready": False, "failed": True, "error": "No EvidenceSpan was extracted from the imported paper."}
        embed_tasks = [
            task
            for task in self.db.execute(
                select(Task).where(Task.task_type == "embed_chunks").order_by(Task.updated_at.desc())
            ).scalars()
            if (task.payload or {}).get("paper_id") == paper.id
        ]
        latest_embed = embed_tasks[0] if embed_tasks else None
        if latest_embed is None or latest_embed.status in PIPELINE_PENDING_STATUSES:
            return {"ready": False, "failed": False, "error": "Vector indexing is still running."}
        if latest_embed.status == "failed":
            return {"ready": False, "failed": True, "error": latest_embed.error or "Vector indexing failed."}
        if latest_embed.status != "succeeded":
            return {"ready": False, "failed": False, "error": "Vector indexing has not completed."}
        return {"ready": True, "failed": False, "error": None}

    def _external_candidate_state(self, run: DiscoverRun) -> dict[str, Any]:
        rows = list(
            self.db.execute(
                select(DiscoverExternalCandidate)
                .where(DiscoverExternalCandidate.discover_run_id == run.id)
                .order_by(DiscoverExternalCandidate.rank)
            ).scalars()
        )
        for row in rows:
            if row.imported_paper_id and row.verification_status in {
                "selected", "imported_pending_parse", "verification_failed"
            }:
                state = self._paper_pipeline_state(row.imported_paper_id)
                if state["ready"]:
                    row.verification_status = "verified"
                    row.evidence_level = "full_text"
                elif state["failed"]:
                    row.verification_status = "verification_failed"
                    row.snapshot_payload = {
                        **(row.snapshot_payload or {}),
                        "verification_error": state["error"],
                    }
                else:
                    row.verification_status = "imported_pending_parse"
        self.db.commit()
        return {
            "selected": sum(row.verification_status == "selected" for row in rows),
            "pending": sum(row.verification_status == "imported_pending_parse" for row in rows),
            "verified": sum(row.verification_status == "verified" for row in rows),
            "failed": sum(row.verification_status in {"no_pdf", "import_failed", "verification_failed"} for row in rows),
            "rows": rows,
        }

    def _wait_for_fulltext(self, run: DiscoverRun, state: dict[str, Any]) -> dict[str, Any]:
        self.db.refresh(run)
        if self.service and self.service._cancelled(run):
            return self.service._cancelled_result(run)
        pending = int(state.get("pending", 0))
        failed = int(state.get("failed", 0))
        if pending:
            run.status = "waiting_for_fulltext"
            run.stage = "fulltext_verification"
            run.progress = max(run.progress, 0.68)
            run.verification_status = "in_progress"
            summary = {
                "status": "waiting_for_fulltext",
                "pending": pending,
                "verified": int(state.get("verified", 0)),
                "failed": failed,
                "message": "Waiting for PDF parsing, knowledge extraction, and vector indexing.",
            }
        else:
            run.status = "waiting_for_user"
            run.stage = "external_selection"
            run.progress = max(run.progress, 0.62)
            run.verification_status = "failed" if failed else "incomplete"
            summary = {
                "status": "waiting_for_user",
                "pending": 0,
                "verified": int(state.get("verified", 0)),
                "failed": failed,
                "message": "Select another candidate or retry the failed full-text verification.",
            }
        run.stage_summaries = {**(run.stage_summaries or {}), "fulltext_verification": summary}
        self.db.commit()
        if run.task_id:
            try:
                TaskService(self.db).transition(run.task_id, "waiting_for_user", progress=run.progress)
            except Exception:
                pass
        return {
            "run_id": run.id,
            "status": run.status,
            "waiting_for_fulltext": pending > 0,
            "waiting_for_user": pending == 0,
            "verification": summary,
        }

# ---------------------------------------------------------- query 构建
    def _external_query_text(self, item: KnowledgeItem) -> str:
        """将 KnowledgeItem 渲染为外部搜索查询字符串。

        method 使用描述性名称：多词 canonical name 原样使用；全大写缩写在可用时根据描述开头的
        名词短语展开（例如 ``IRM`` -> “Invariant Risk Minimization”）。limitation 使用短的
        canonical name（caveats -> counter-evidence）；claim 使用其 statement。
        """
        content = item.content or {}
        if item.type == "claim":
            return self.service._claim_text(item) if self.service else ""
        if item.type == "method":
            name = item.canonical_name.strip()
            if len(name.split()) >= 2 or not re.fullmatch(r"[A-Z]{2,5}", name):
                return name
            description = content.get("description")
            if isinstance(description, str) and description.strip():
                match = re.match(r"[A-Z][a-zA-Z0-9-]*(?:\s+[A-Z][a-zA-Z0-9-]*){1,3}", description.strip())
                full = match.group(0) if match else ""
                first = full.split()[0].lower() if full else ""
                if full and len(full.split()) >= 2 and full.lower() != name.lower() and first not in {"a", "an", "the"}:
                    return full
            return name
        # limitation 和 task：较短的 canonical name 携带的信号最强；过长的 description
        # 会稀释 S2 relevance matching。
        return item.canonical_name.strip()

    def _external_query_signal_items(self, workspace_id: str, types: tuple[str, ...] | None = None) -> list[KnowledgeItem]:
        """按对外部查询的有用程度排列工作区项。

        method 优先（命名实体是最强的外部搜索键），降低架构组件（Pool/Module/Layer）和括号子模块别名
       （“Self-Denoising (SD)”）的优先级，使真实命名方法（GIB、IRM、SubgraphX、GSAT…）出现；
        之后是 limitation（caveats -> counter-evidence）、claim、task。跳过 rejected 和低置信度项，
        避免噪声抽取污染外部搜索。
        """
        types = types or EXTERNAL_QUERY_SIGNAL_TYPES
        items = list(
            self.db.execute(
                select(KnowledgeItem).where(
                    KnowledgeItem.workspace_id == workspace_id,
                    KnowledgeItem.is_deleted.is_(False),
                    KnowledgeItem.status != "rejected",
                    KnowledgeItem.type.in_(types),
                    KnowledgeItem.confidence >= EXTERNAL_QUERY_MIN_CONFIDENCE,
                )
            ).scalars()
        )
        type_rank = {"limitation": 0, "claim": 1, "task": 2}

        def is_component(item: KnowledgeItem) -> bool:
            words = {w.lower() for w in re.findall(r"[A-Za-z]+", item.canonical_name)}
            return bool(words & EXTERNAL_METHOD_COMPONENT_TOKENS)

        def is_parens_alias(item: KnowledgeItem) -> bool:
            return "(" in item.canonical_name or ")" in item.canonical_name

        def priority(item: KnowledgeItem) -> tuple[float, float]:
            if item.type == "method":
                if is_component(item):
                    return (3.0, -float(item.confidence or 0.0))
                if is_parens_alias(item):
                    return (2.0, -float(item.confidence or 0.0))
                return (0.0, -float(item.confidence or 0.0))
            return (type_rank.get(item.type, 9) + 10.0, -float(item.confidence or 0.0))

        items.sort(key=priority)
        return items

    def _external_query_signal_texts(self, workspace_id: str, *, max_methods: int = 24, max_limitations: int = 6, max_claims: int = 6) -> str:
        """将工作区信号压缩为 axis-query LLM 提示词文本。"""
        lines: list[str] = []
        methods = self._external_query_signal_items(workspace_id, types=("method",))[:max_methods]
        if methods:
            lines.append("Methods: " + "; ".join(self._external_query_text(m) for m in methods))
        limitations = self._external_query_signal_items(workspace_id, types=("limitation",))[:max_limitations]
        if limitations:
            lines.append("Limitations: " + "; ".join(self._external_query_text(lim) for lim in limitations))
        claims = self._external_query_signal_items(workspace_id, types=("claim",))[:max_claims]
        if claims:
            lines.append("Claims: " + "; ".join(self._external_query_text(cl) for cl in claims))
        return "\n".join(lines)

    def _external_method_full_names(self, workspace_id: str, *, max_names: int = 40) -> list[str]:
        """去重后的方法全名查询，用于为 LLM 研究轴查询提供依据。"""
        names: list[str] = []
        seen: set[str] = set()
        for item in self._external_query_signal_items(workspace_id, types=("method",)):
            name = self._external_query_text(item).strip()
            key = name.lower()
            if len(name) < 4 or key in seen:
                continue
            seen.add(key)
            names.append(name)
            if len(names) >= max_names:
                break
        return names

    def _axis_queries_from_llm(self, run: DiscoverRun, primary: str) -> tuple[list[str], list[str]]:
        """将研究问题分解为外部搜索查询（LLM）。

        长段落不适合作为相关性搜索查询。LLM 会将研究问题转换为简洁的关键词查询，
        分别面向基础、重叠、反证和评测文献，并使用工作区抽取的方法和局限作为上下文。
        它还会选择最多 2 个工作区方法名进行精确标题查找（``exact_lookups``）。
        LLM 失败或响应格式错误时返回 ``([], [])``，使调用方回退到工作区信号查询。
        """
        signals = self._external_query_signal_texts(run.workspace_id)
        user_prompt = (
            f"RESEARCH QUESTION: {primary[:300]}\n\n"
            f"WORKSPACE SIGNALS (methods / limitations / claims to consider):\n"
            f"{signals if signals else '(none extracted)'}\n\n"
            f"Generate {EXTERNAL_QUERY_AXIS_COUNT} concise external-search queries "
            f"(3-8 words each). Include at least 2 queries derived from the "
            f"workspace method names, expanding abbreviations to their full names.\n"
            f"MUST include: at least 1 query targeting COUNTER-EVIDENCE / critique "
            f"literature for the question's core claims (e.g. fragility, sanity "
            f"checks, reliability or uncertainty of the claimed properties), and "
            f"at least 1 query targeting EVALUATION / benchmark literature for "
            f"those claims. These two angles are mandatory even when the workspace "
            f"signals suggest other axes.\n"
            f"IMPORTANT: critique and evaluation literature usually lives OUTSIDE "
            f"the question's narrow domain — search the broader field (e.g. for a "
            f"graph question, also query general explainability / XAI critique "
            f"papers), not only domain-specific variants."
        )
        try:
            resp = self.llm.chat_completion(
                [
                    {"role": "system", "content": EXTERNAL_QUERY_AXIS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=800,
                disable_thinking=True,
            )
            accumulate_tokens(run, resp)
            parsed = parse_json(resp.content)
            if not isinstance(parsed, dict):
                logger.warning("discover.external_axis_query_bad_shape", raw_preview=(resp.content or "")[:200])
                return [], []
            queries: list[str] = []
            for q in parsed.get("queries") or []:
                if isinstance(q, str) and q.strip():
                    queries.append(q.strip())
            lookups: list[str] = []
            for name in parsed.get("exact_lookups") or []:
                if isinstance(name, str) and name.strip():
                    lookups.append(name.strip())
            return (
                queries[:EXTERNAL_QUERY_AXIS_COUNT],
                lookups[:EXTERNAL_QUERY_MAX_EXACT_LOOKUPS],
            )
        except Exception as exc:
            logger.warning("discover.external_axis_query_failed", error=str(exc))
            return [], []

    def _external_query_plan(self, run: DiscoverRun, primary: str) -> tuple[list[str], list[str]]:
        """构建外部搜索查询和精确查找名称。

        返回 ``(queries, exact_lookups)``。主查询是本次运行的 claim/topic（即研究问题本身）。
        LLM 将研究问题分解为简洁的轴查询（基础方法、反证、评测/批评、领域轴），
        并选择最多 2 个方法名进行精确标题查找。LLM 失败或需要填充剩余预算时，使用工作区信号：
        先方法名，再 limitation/claim/task，最后才使用用户通用关键词。查询会去重并受
        ``EXTERNAL_QUERY_MAX_TOTAL`` 限制。
        """
        queries: list[str] = []
        seen: set[str] = set()

        def add(text: str) -> bool:
            text = text.strip()
            if not text or text.lower() in seen:
                return False
            queries.append(text[:200])
            seen.add(text.lower())
            return True

        axis, lookups = self._axis_queries_from_llm(run, primary)
        add(primary)
        for axis_query in axis:
            add(axis_query)
            if len(queries) >= EXTERNAL_QUERY_MAX_TOTAL:
                return queries, lookups
        # 补充 LLM 引用的方法名称：类似 "graph information bottleneck sufficiency necessity" 的
        # 修饰型 query 虽能找到 rationalization 论文，却经常漏掉方法本身的论文。方法完整
        # 名称与 S2 relevance 的匹配效果好得多，因此 axis query 中出现的每个方法名称也
        # 会作为干净 query 加入（去重后）。
        method_names = self._external_method_full_names(run.workspace_id)
        lookup_set = {name.strip().lower() for name in lookups if name.strip()}
        for axis_query in axis:
            axis_lower = axis_query.lower()
            for name in method_names:
                if len(name) >= 4 and name.lower() in axis_lower:
                    # Exact lookup 已经精确获取这些方法论文；重复的 relevance query 只会
                    # 消耗 query budget。
                    if name.strip().lower() in lookup_set:
                        continue
                    if add(name) and len(queries) >= EXTERNAL_QUERY_MAX_TOTAL:
                        return queries, lookups
        for item in self._external_query_signal_items(run.workspace_id, types=("method",)):
            add(self._external_query_text(item))
            if len(queries) >= EXTERNAL_QUERY_MAX_TOTAL:
                return queries, lookups
        for item in self._external_query_signal_items(run.workspace_id, types=("limitation", "claim", "task")):
            add(self._external_query_text(item))
            if len(queries) >= EXTERNAL_QUERY_MAX_TOTAL:
                return queries, lookups
        keyword_count = 0
        for kw in (run.input_payload or {}).get("keywords") or []:
            if not isinstance(kw, str) or keyword_count >= EXTERNAL_QUERY_MAX_KEYWORDS:
                continue
            if add(kw):
                keyword_count += 1
            if len(queries) >= EXTERNAL_QUERY_MAX_TOTAL:
                break
        return queries, lookups

    def _build_external_queries(self, run: DiscoverRun, primary: str) -> list[str]:
        """围绕 ``_external_query_plan`` 的向后兼容列表包装器。"""
        return self._external_query_plan(run, primary)[0]

    @staticmethod
    def _query_purpose(query: str, index: int) -> str:
        """无需再次调用 LLM，为查询分配稳定的审计用途。

        轴查询提示词刻意返回简洁字符串，而不是第二个 schema。位置用于识别主问题；
        特有的反证/评测词用于识别高价值轴，其余查询用于方法/重叠探索。
        """

        if index == 0:
            return "primary_question"
        tokens = set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", query.lower()))
        if tokens & EXTERNAL_COUNTER_TOKENS:
            return "counter_evidence"
        if tokens & EXTERNAL_EVALUATION_TOKENS:
            return "evaluation"
        return "method_overlap"

# ---------------------------------------------------------------- 校验
    def _external_verify(
        self,
        run: DiscoverRun,
        queries: list[str],
        exact_lookups: list[str] | None = None,
    ) -> int:
        """通过多个查询搜索 Semantic Scholar 并合并候选项。

        ``queries[0]`` 是本次运行的研究问题（claim/topic）；其余查询是
        ``_external_query_plan`` 构建的额外角度。结果按 ``external_paper_id`` 去重并分配新的连续排名。
        每个候选项都会记录发现它的查询，因此审计轨迹可以显示哪个工作区信号产生了哪个候选项。

        ``exact_lookups`` 是按精确标题搜索并校验标题的方法名——相关性搜索可能被轴后缀词稀释，
        因此 LLM 选择的方法名会经过一次精确校验流程，其命中项会置于合并候选项之前。
        """
        existing = int(self.db.execute(select(func.count()).select_from(DiscoverExternalCandidate).where(DiscoverExternalCandidate.discover_run_id == run.id)).scalar() or 0)
        if existing:
            rows = list(self.db.execute(select(DiscoverExternalCandidate).where(DiscoverExternalCandidate.discover_run_id == run.id)).scalars())
            external_summary = (run.stage_summaries or {}).get("external_search")
            if not isinstance(external_summary, dict) or external_summary.get("status") not in {
                "succeeded",
                "succeeded_partial",
                "succeeded_empty",
            }:
                run.stage_summaries = {
                    **(run.stage_summaries or {}),
                    "external_search": {
                        **(external_summary if isinstance(external_summary, dict) else {}),
                        "status": "succeeded",
                        "executed": True,
                        "candidate_count": existing,
                    },
                }
                self.db.commit()
            self._external_candidate_state(run)
            return existing
        queries = [q.strip() for q in queries if q and q.strip()]
        exact_lookups = [q.strip() for q in exact_lookups or [] if q and q.strip()]
        if not queries and not exact_lookups:
            run.verification_status = "incomplete"
            self.db.commit()
            return 0
        primary = queries[0] if queries else (run.input_topic or "")
        config = DiscoverConfig.model_validate(run.config or {})
        top_k = config.top_k
        scope = DiscoverScope.model_validate(run.scope or {})
        year = None
        if scope.year_from is not None or scope.year_to is not None:
            year = f"{scope.year_from or ''}-{scope.year_to or ''}"
        per_query: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = []
        query_failures: list[dict[str, Any]] = []
        query_records: list[dict[str, Any]] = []
        for index, query in enumerate(queries):
            purpose = self._query_purpose(query, index)
            try:
                # 每个 query 都获取完整的 top_k：gate 的 recall 受每个 query 的截断限制，
                # 而合并过程本身会去重。（无论 limit 参数如何变化，S2 API 调用次数相同。）
                limit = top_k
                raw = self.external_search.search(
                    query=query[:200],
                    fields=S2_FIELDS,
                    sort="relevance",
                    limit=limit,
                    year=year,
                )
                seen_in_query: set[str] = set()
                q_results: list[tuple[str, dict[str, Any]]] = []
                for item in raw.get("data") or []:
                    if not isinstance(item, dict) or not item.get("paperId") or not item.get("title"):
                        continue
                    pid = str(item["paperId"])
                    if pid not in seen_in_query:
                        seen_in_query.add(pid)
                        q_results.append((pid, item))
                per_query.append((query, q_results))
                query_records.append(
                    {
                        "query": query[:120],
                        "purpose": purpose,
                        "status": "succeeded",
                        "result_count": len(q_results),
                    }
                )
            except SemanticScholarError as exc:
                failure = {
                    "query": query[:120],
                    "purpose": purpose,
                    "status": "failed",
                    "error": str(exc),
                    "status_code": exc.status_code,
                    "failure_kind": getattr(exc, "failure_kind", None)
                    or semantic_scholar_failure_kind(exc.status_code),
                    "retryable": exc.status_code in {429, 500, 502, 503, 504},
                }
                query_failures.append(failure)
                query_records.append(failure)
                logger.warning(
                    "discover.external_query_failed",
                    run_id=run.id,
                    query=query[:120],
                    error=str(exc),
                )

        if queries and not per_query and not exact_lookups:
            last_failure = query_failures[-1] if query_failures else {}
            run.verification_status = "failed"
            run.stage_summaries = {
                **(run.stage_summaries or {}),
                "external_search": {
                    "status": "failed",
                    "error": last_failure.get("error", "all external search queries failed"),
                    "retryable": any(item["retryable"] for item in query_failures),
                    "executed": False,
                    "queries": [q[:120] for q in queries],
                    "successful_query_count": 0,
                    "failed_query_count": len(query_failures),
                    "query_success_rate": 0.0,
                    "query_records": query_records,
                    "query_failures": query_failures,
                    "failure_counts": dict(Counter(item.get("failure_kind", "request_error") for item in query_failures)),
                    "notice_level": "critical",
                    "impact": "all_queries_failed",
                    "message": "外部检索全部失败，未获得可用候选论文。",
                },
            }
            self.db.commit()
            logger.warning(
                "discover.external_search_failed",
                run_id=run.id,
                error=last_failure.get("error", "all external search queries failed"),
            )
            return 0

        # 对 LLM 选定的方法名称执行 exact-title lookup（已校验 title）。
        # 采用 best-effort：lookup 失败只跳过该名称，不会使整个 run 失败。
        lookup_hits: list[tuple[str, dict[str, Any], str]] = []
        exact_lookup_records: list[dict[str, Any]] = []
        exact_lookup_failures: list[dict[str, Any]] = []
        for name in exact_lookups or []:
            name = (name or "").strip()
            if not name:
                continue
            lookup_record: dict[str, Any] = {
                "query": name[:120],
                "purpose": "exact_lookup",
                "status": "no_verified_match",
                "result_count": 0,
            }
            try:
                raw = self.external_search.search(
                    query=name[:200],
                    fields=S2_FIELDS,
                    sort="relevance",
                    limit=2,
                    year=year,
                )
            except SemanticScholarError as exc:
                lookup_record.update(
                    {
                        "status": "failed",
                        "error": str(exc),
                        "status_code": exc.status_code,
                        "failure_kind": getattr(exc, "failure_kind", None)
                        or semantic_scholar_failure_kind(exc.status_code),
                    "retryable": exc.status_code in RETRYABLE_EXTERNAL_STATUS_CODES,
                    }
                )
                exact_lookup_failures.append(lookup_record)
                exact_lookup_records.append(lookup_record)
                continue
            for item in raw.get("data") or []:
                if not isinstance(item, dict) or not item.get("paperId") or not item.get("title"):
                    continue
                if not title_verified(name, str(item["title"])):
                    continue
                lookup_hits.append((str(item["paperId"]), item, f"exact: {name[:120]}"))
                lookup_record.update({"status": "succeeded", "result_count": 1})
                break  # 每个 lookup name 只保留一篇已验证论文
            exact_lookup_records.append(lookup_record)

        # 在 query 之间执行 reciprocal-rank fusion，使合并后的 top-K 体现跨 query 的一致性，
        # 而不是 query 顺序（P2-4：旧的 round-robin append 曾将 critique-axis query 的首个
        # 结果排到约第 31 位，尽管该 query 几乎就是论文标题）。被多个 query 找到的论文
        # 优先于同一位置的单 query 命中；平分时使用 citation count，让基础经典论文优先
        # 于偶然匹配。已验证的 lookup 命中继续置顶，因为它们是最确定的匹配。
        merged: list[tuple[str, dict[str, Any], str]] = []
        seen: set[str] = set()
        for pid, item, source_query in lookup_hits:
            if pid not in seen:
                seen.add(pid)
                merged.append((pid, item, source_query))
        rrf_scores: dict[str, float] = {}
        rrf_citations: dict[str, int] = {}
        rrf_first: dict[str, tuple[int, str, dict[str, Any]]] = {}
        rrf_best_pos: dict[str, int] = {}
        rrf_best_source: dict[str, str] = {}
        rrf_item: dict[str, dict[str, Any]] = {}
        for order, (source_query, q_results) in enumerate(per_query):
            for position, (pid, item) in enumerate(q_results, start=1):
                rrf_scores[pid] = rrf_scores.get(pid, 0.0) + 1.0 / (RRF_K + position)
                citations = item.get("citationCount")
                rrf_citations[pid] = max(
                    rrf_citations.get(pid, 0), citations if isinstance(citations, int) else 0
                )
                rrf_first.setdefault(pid, (order, source_query[:200], item))
                rrf_item.setdefault(pid, item)
                if position < rrf_best_pos.get(pid, position + 1):
                    rrf_best_pos[pid] = position
                    rrf_best_source[pid] = source_query[:200]
        fused = sorted(
            (pid for pid in rrf_scores if pid not in seen),
            key=lambda pid: (-rrf_scores[pid], -rrf_citations.get(pid, 0), rrf_first[pid][0]),
        )
        for pid in fused:
            merged.append((pid, rrf_item[pid], rrf_best_source.get(pid) or rrf_first[pid][1]))

        rows: list[DiscoverExternalCandidate] = []
        for rank, (pid, item, source_query) in enumerate(merged, start=1):
            authors = [a.get("name", "") for a in item.get("authors") or [] if isinstance(a, dict) and a.get("name")]
            row = DiscoverExternalCandidate(
                id=str(uuid4()), discover_run_id=run.id, query=source_query,
                rank=rank,
                external_paper_id=pid, title=str(item["title"]), authors=authors,
                year=item.get("year") if isinstance(item.get("year"), int) else None,
                abstract=item.get("abstract") if isinstance(item.get("abstract"), str) else None,
                open_access_pdf=item.get("openAccessPdf") if isinstance(item.get("openAccessPdf"), dict) else None,
                role=external_role(primary, item), role_confidence=0.35,
                evidence_level="metadata_only", verification_status="unverified", snapshot_payload=item,
            )
            rows.append(row)
        self.db.add_all(rows)
        candidate_count = len(rows)
        run.verification_status = "in_progress" if rows else "incomplete"
        query_total = len(queries)
        successful_query_count = len(per_query)
        query_success_rate = successful_query_count / query_total if query_total else 0.0
        failed_purposes = {item["purpose"] for item in query_failures}
        critical_failure = bool(failed_purposes & {"primary_question", "counter_evidence"})
        low_query_success = query_success_rate < EXTERNAL_QUERY_MIN_SUCCESS_RATE
        insufficient_candidates = len(rows) < EXTERNAL_MIN_CANDIDATES_FOR_CLEAN_STATUS
        warning = critical_failure or low_query_success or insufficient_candidates
        if not rows:
            search_status = "succeeded_empty"
        elif warning:
            search_status = "succeeded_partial"
        else:
            search_status = "succeeded"
        notice_level = "warning" if warning else ("informational" if query_failures or exact_lookup_failures else "none")
        impact = (
            "critical_query_failed"
            if critical_failure
            else "low_query_success_rate"
            if low_query_success
            else "candidate_shortage"
            if insufficient_candidates
            else "non_critical_query_limited"
            if query_failures or exact_lookup_failures
            else "none"
        )
        if query_failures:
            failure_counts = Counter(item.get("failure_kind", "request_error") for item in query_failures)
            failure_labels = {
                "rate_limited": "频率限制",
                "timeout": "请求超时",
                "network_error": "网络/TLS异常",
                "upstream_error": "外部服务异常",
                "request_error": "请求异常",
            }
            reason_text = "、".join(
                f"{failure_labels.get(kind, '请求异常')} {count} 条"
                for kind, count in failure_counts.most_common()
            )
            message = (
                f"已完成 {successful_query_count}/{query_total} 个检索方向，"
                f"已保留 {candidate_count} 篇候选。未完成原因：{reason_text}。"
            )
        else:
            message = "外部检索完成，已获得候选论文。"
        if exact_lookup_failures:
            message += f"另有 {len(exact_lookup_failures)} 条方法精确查找未完成。"
        if insufficient_candidates and rows:
            message += "候选数量偏少，仍需谨慎核验。"
        run.stage_summaries = {
            **(run.stage_summaries or {}),
            "external_search": {
                "status": search_status,
                "executed": True,
                "candidate_count": candidate_count,
                "queries": [q[:120] for q in queries],
                "successful_query_count": successful_query_count,
                "failed_query_count": len(query_failures),
                "query_success_rate": round(query_success_rate, 4),
                "query_records": [*query_records, *exact_lookup_records],
                "query_failures": query_failures,
                "failure_counts": dict(Counter(item.get("failure_kind", "request_error") for item in query_failures)),
                "exact_lookup_count": len(exact_lookup_records),
                "exact_lookup_failure_count": len(exact_lookup_failures),
                "exact_lookup_failures": exact_lookup_failures,
                "notice_level": notice_level,
                "impact": impact,
                "message": message,
            },
        }
        self.db.commit()
        # 使用 LLM 优化候选角色（similar/overlap/qualify/contradict/unknown）；heuristic 只提供
        # similar/unknown。失败时保留 heuristic role，候选仍可审计。角色判断依据是研究问题
        # （主查询）。
        if rows:
            self._judge_external_roles(run, primary, rows)
        return len(rows)

    # -------------------------------------------------------------- 角色判断
    def _judge_external_roles(
        self,
        run: DiscoverRun,
        query: str,
        candidates: list[DiscoverExternalCandidate],
    ) -> None:
        """使用 LLM 优化外部候选项角色。

        ``external_role`` 是只返回 similar/unknown 的低成本词重叠启发式规则。
        Stage 3 需要区分 similar / overlap / qualify / contradict / unknown，
        使 Discover 能识别哪篇外部论文可能*挑战*一个机会，而不只是与它相似。

        该方法使用 LLM 网关批量对照研究问题判断候选项。失败时静默保留启发式角色
        （候选行已经携带 ``external_role`` 产生的角色）。
        """
        if not candidates:
            return
        gateway = self.llm
        batch_size = 8
        role_map = {
            "similar": "similar",
            "overlaps": "overlap",
            "overlap": "overlap",
            "qualifies": "qualifies",
            "qualify": "qualifies",
            "contradicts": "contradicts",
            "contradict": "contradicts",
            "unknown": "unknown",
        }

        for start in range(0, len(candidates), batch_size):
            batch = candidates[start : start + batch_size]
            lines = [
                f"[{i}] {c.title or ''} — {(c.abstract or '')[:400]}"
                for i, c in enumerate(batch)
            ]
            user_prompt = (
                f"RESEARCH QUESTION: {query[:300]}\n\nCANDIDATES:\n" + "\n".join(lines)
            )
            try:
                resp = gateway.chat_completion(
                    [
                        {"role": "system", "content": EXTERNAL_ROLE_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.0,
                    max_tokens=2000,
                    disable_thinking=True,
                )
                accumulate_tokens(run, resp)
                parsed = parse_json(resp.content)
                items = parsed.get("roles") if isinstance(parsed, dict) else None
                if not isinstance(items, list):
                    logger.warning("discover.external_role_bad_shape", raw_preview=(resp.content or "")[:200])
                    continue
                for hit in items:
                    if not isinstance(hit, dict):
                        continue
                    idx = hit.get("index")
                    if not isinstance(idx, int) or not (0 <= idx < len(batch)):
                        continue
                    role = str(hit.get("role", "unknown")).lower()
                    candidate = batch[idx]
                    candidate.role = role_map.get(role, "unknown")
                    try:
                        candidate.role_confidence = float(hit.get("confidence", 0.3))
                    except (TypeError, ValueError):
                        candidate.role_confidence = 0.3
            except Exception as exc:
                logger.warning("discover.external_role_judge_failed", error=str(exc))
                # 失败时保留 heuristic role（该角色已设置在各行上）。
        # 所有 batch 完成后一次性持久化优化后的角色。
        self.db.commit()

    def _judge_external_fulltext_roles(self, run: DiscoverRun, query: str) -> int:
        """尽力重新判断已验证全文候选项的角色（W1）。

        导入论文的解析文本可用后，再优化元数据级角色（标题+摘要）。该操作是幂等的：
        已标记 ``fulltext_role_judged`` 的行会跳过；LLM 失败时保留元数据角色并标记
        ``fulltext_role_tried``，以便后续恢复时重试且不会无限循环。
        """
        rows = list(
            self.db.execute(
                select(DiscoverExternalCandidate).where(
                    DiscoverExternalCandidate.discover_run_id == run.id,
                    DiscoverExternalCandidate.verification_status == "verified",
                    DiscoverExternalCandidate.imported_paper_id.is_not(None),
                )
            ).scalars()
        )
        to_judge = [
            row for row in rows if not (row.snapshot_payload or {}).get("fulltext_role_judged")
        ]
        if not to_judge:
            return 0
        role_map = {
            "similar": "similar",
            "overlap": "overlap",
            "overlaps": "overlap",
            "qualifies": "qualifies",
            "qualify": "qualifies",
            "contradicts": "contradicts",
            "contradict": "contradicts",
            "unknown": "unknown",
        }
        judged = 0
        for row in to_judge:
            paper = self.db.get(Paper, row.imported_paper_id)
            if paper is None or not paper.parsed_text_artifact_id:
                continue
            text = self._read_paper_text(paper)[:4000]
            if not text.strip():
                continue
            try:
                resp = self.llm.chat_completion(
                    [
                        {"role": "system", "content": EXTERNAL_FULLTEXT_ROLE_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": f"RESEARCH QUESTION: {query[:300]}\n\nFULL TEXT:\n{text}",
                        },
                    ],
                    temperature=0.0,
                    max_tokens=500,
                    disable_thinking=True,
                )
                parsed = parse_json(resp.content)
                role = str((parsed or {}).get("role", "unknown")).lower()
                row.role = role_map.get(role, "unknown")
                try:
                    row.role_confidence = float((parsed or {}).get("confidence", 0.5))
                except (TypeError, ValueError):
                    row.role_confidence = 0.5
                row.snapshot_payload = {
                    **(row.snapshot_payload or {}),
                    "fulltext_role_judged": True,
                    "fulltext_role": row.role,
                }
                judged += 1
            except Exception as exc:
                logger.warning("discover.external_fulltext_role_failed", error=str(exc))
                row.snapshot_payload = {
                    **(row.snapshot_payload or {}),
                    "fulltext_role_tried": True,
                }
        self.db.commit()
        return judged

    def _read_paper_text(self, paper: Paper) -> str:
        """尽力读取已导入论文的解析纯文本。"""
        if not paper.parsed_text_artifact_id:
            return ""
        artifact = self.db.get(Artifact, paper.parsed_text_artifact_id)
        if artifact is None or artifact.is_deleted:
            return ""
        try:
            return ArtifactService(self.db).resolve_abs_path(artifact).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            return ""

# ----------------------------------------------------------------- 导入
    def _candidate_pdf_urls(self, row: DiscoverExternalCandidate) -> list[tuple[str, str]]:
        """返回一个候选项的有序去重 PDF 来源。

        ``openAccessPdf`` 是提供商提示，不是保证。当前从持久化 S2 标识符中唯一可确定的回退来源是
        arXiv；落地页地址不会被当作 PDF。
        """
        raw = row.snapshot_payload or {}
        sources: list[tuple[str, str]] = []
        pdf = row.open_access_pdf or {}
        if isinstance(pdf, dict) and isinstance(pdf.get("url"), str):
            sources.append(("semantic_scholar_open_access", pdf["url"]))

        external_ids = raw.get("externalIds") if isinstance(raw.get("externalIds"), dict) else {}
        arxiv_id = (
            external_ids.get("ArXiv")
            or external_ids.get("ARXIV")
            or external_ids.get("arXiv")
        )
        if isinstance(arxiv_id, str) and arxiv_id.strip():
            arxiv_id = arxiv_id.removeprefix("arXiv:").removesuffix(".pdf").strip()
            sources.append(("arxiv", f"https://arxiv.org/pdf/{quote(arxiv_id, safe='/')}"))

        unique: list[tuple[str, str]] = []
        seen: set[str] = set()
        for source, url in sources:
            normalized = normalize_pdf_url(url)
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique.append((source, normalized))
        return unique

    def _import_selected_candidates(self, run: DiscoverRun) -> None:
        """尽力导入用户选择的 OA PDF。

        导入过程刻意保持显式：仅元数据候选项绝不会变成全文证据。
        解析/索引仍由现有 worker 流水线执行，候选项在完成前保持可见的 pending 状态。
        """
        client = SemanticScholarClient()
        rows = list(self.db.execute(select(DiscoverExternalCandidate).where(DiscoverExternalCandidate.discover_run_id == run.id, DiscoverExternalCandidate.verification_status == "selected")).scalars())
        for row in rows:
            if row.imported_paper_id:
                self._ensure_paper_pipeline(run.workspace_id, row.imported_paper_id)
                continue
            raw = row.snapshot_payload or {}
            pdf_sources = self._candidate_pdf_urls(row)
            if not pdf_sources:
                row.verification_status = "no_pdf"
                row.snapshot_payload = {
                    **raw,
                    "pdf_acquisition": {
                        "status": "no_pdf",
                        "attempts": [],
                        "message": "未找到可用的开放获取 PDF 地址。",
                    },
                }
                continue

            content: bytes | None = None
            attempts: list[dict[str, Any]] = []
            last_error: SemanticScholarError | None = None
            for source, pdf_url in pdf_sources:
                try:
                    content = client.download_pdf(pdf_url)
                    attempts.append({"source": source, "url": pdf_url, "status": "succeeded"})
                    break
                except SemanticScholarError as exc:
                    last_error = exc
                    attempts.append(
                        {
                            "source": source,
                            "url": pdf_url,
                            "status": "failed",
                            "status_code": exc.status_code,
                            "failure_kind": getattr(exc, "failure_kind", None),
                        "retryable": exc.status_code in RETRYABLE_EXTERNAL_STATUS_CODES,
                            "error": str(exc)[:300],
                        }
                    )

            if content is None:
                error = str(last_error)[:500] if last_error else "没有可用 PDF"
                retryable = any(item.get("retryable") for item in attempts)
                row.verification_status = "import_failed"
                row.snapshot_payload = {
                    **raw,
                    "import_error": error,
                    "pdf_acquisition": {
                        "status": "retryable_failure" if retryable else "unavailable",
                        "attempts": attempts,
                        "message": "PDF 下载暂时失败，可稍后重试。" if retryable else "未能从可用来源获取有效 PDF。",
                    },
                }
                continue

            try:
                paper_service = PaperService(self.db)
                paper = paper_service.find_by_external_paper_id(workspace_id=run.workspace_id, external_paper_id=row.external_paper_id)
                if paper is None:
                    paper = paper_service.create_from_metadata(workspace_id=run.workspace_id, payload=PaperCreate(title=row.title, authors=row.authors, year=row.year, abstract=row.abstract), source="semantic_scholar", external_paper_id=row.external_paper_id)
                if paper.primary_artifact_id is None:
                    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", row.title)[:120] or row.external_paper_id
                    paper = paper_service.attach_pdf_to_existing(workspace_id=run.workspace_id, paper_id=paper.id, filename=f"{filename}.pdf", content=content, mime_type="application/pdf")
                row.imported_paper_id = paper.id
                row.verification_status = "imported_pending_parse"
                row.evidence_level = "metadata_only"
            except (SemanticScholarError, ValueError) as exc:
                row.verification_status = "import_failed"
                row.snapshot_payload = {
                    **raw,
                    "import_error": str(exc)[:500],
                    "pdf_acquisition": {
                        "status": "local_import_failed",
                        "attempts": attempts,
                    },
                }
        self.db.commit()

    def _ensure_paper_pipeline(self, workspace_id: str, paper_id: str) -> None:
        """安全地仅重启缺失或失败的现有流水线阶段。"""
        paper = self.db.get(Paper, paper_id)
        if paper is None or paper.is_deleted:
            return
        active = list(
            self.db.execute(
                select(Task).where(
                    Task.workspace_id == workspace_id,
                    Task.status.in_(PIPELINE_PENDING_STATUSES),
                    Task.is_deleted.is_(False),
                )
            ).scalars()
        )

        def has_active(task_type: str) -> bool:
            return any(
                task.task_type == task_type
                and (task.payload or {}).get("paper_id") == paper_id
                for task in active
            )

        if paper.primary_artifact_id and paper.parse_status in {"pending", "failed", "parsing"} and not has_active("parse_pdf"):
            from app.workers.tasks.parse_pdf import spawn_parse_pdf_task

            paper.parse_status = "pending"
            self.db.commit()
            spawn_parse_pdf_task(self.db, paper_id, workspace_id)
            return
        if paper.parsed_markdown_artifact_id and paper.extract_status in {"pending", "failed", "extracting", "not_applicable"} and not has_active("extract_knowledge"):
            from app.workers.tasks.extract_knowledge import spawn_extract_knowledge

            spawn_extract_knowledge(self.db, paper_id, workspace_id)
        if paper.parsed_text_artifact_id and not has_active("embed_chunks"):
            latest = next(
                (
                    task
                    for task in self.db.execute(
                        select(Task).where(Task.task_type == "embed_chunks").order_by(Task.updated_at.desc())
                    ).scalars()
                    if (task.payload or {}).get("paper_id") == paper_id
                ),
                None,
            )
            if latest is None or latest.status == "failed":
                from app.workers.tasks.embed_chunks import spawn_embed_chunks

                spawn_embed_chunks(self.db, paper_id, workspace_id)
