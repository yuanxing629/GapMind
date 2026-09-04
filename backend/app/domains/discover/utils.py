"""discover domain 共用的纯辅助函数（MA-1）。

本模块被拆出后，critic.py / synthesis.py / external_retrieval.py 可以共用同一份
``parse_json`` / ``retrieval_payload``，无需重复实现。它是叶子模块：只导入标准库和
``retrieval.schemas``，不会被同级 service 模块以循环依赖的方式引用。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.domains.retrieval.schemas import RetrievalResultItem


def parse_json(content: str) -> dict[str, Any] | None:
    """解析 LLM JSON 响应，并容忍代码围栏/额外文本回退。"""
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", content)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def retrieval_payload(item: RetrievalResultItem) -> dict[str, Any]:
    """用于 synthesis/critic prompt 和审计轨迹的精简检索条目。"""
    return {
        "paper_id": item.paper_id,
        "title": item.paper_title,
        "text": item.text[:900],
        "score": item.score,
        "judgement": item.judgement,
        "evidence_level": item.evidence_level,
    }


def accumulate_tokens(run, response) -> None:
    """将 LLM token 使用量累计到 DiscoverRun（W6-3 审计）。

    将 response 的 prompt/completion token 数量加入
    ``run.stage_summaries["token_usage"]``，使 run 的 LLM 成本可以从数据库行汇总。
    同时兼容 gateway response 对象（``.prompt_tokens``）和普通 dict；没有 usage 时不做处理。
    """
    prompt = getattr(response, "prompt_tokens", None)
    completion = getattr(response, "completion_tokens", None)
    if isinstance(response, dict):
        prompt = response.get("prompt_tokens", prompt)
        completion = response.get("completion_tokens", completion)
    if prompt is None and completion is None:
        return
    summary = dict(run.stage_summaries or {})
    tu = dict(summary.get("token_usage") or {})
    tu["prompt_tokens"] = int(tu.get("prompt_tokens", 0)) + int(prompt or 0)
    tu["completion_tokens"] = int(tu.get("completion_tokens", 0)) + int(completion or 0)
    tu["total_tokens"] = tu["prompt_tokens"] + tu["completion_tokens"]
    summary["token_usage"] = tu
    run.stage_summaries = summary
