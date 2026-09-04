"""外部候选角色判断测试（Stage 3）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.domains.discover.models import DiscoverExternalCandidate, DiscoverRun  # noqa: E402
from app.domains.discover.service import DiscoverService  # noqa: E402


def _candidate(title: str, abstract: str = "abstract text") -> DiscoverExternalCandidate:
    return DiscoverExternalCandidate(
        id=f"cand-{abs(hash(title)) % 100000}",
        discover_run_id="run-1",
        query="q",
        rank=1,
        external_paper_id=f"s2-{abs(hash(title)) % 100000}",
        title=title,
        abstract=abstract,
        role="similar",  # heuristic default
        role_confidence=0.35,
        evidence_level="metadata_only",
        verification_status="unverified",
    )


class _FakeLLM:
    """返回预设 roles 数组，并记录 prompt。"""

    def __init__(self, roles: list[dict]) -> None:
        self.roles = roles
        self.calls: list[list[dict]] = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append(messages)
        from types import SimpleNamespace
        payload = {"roles": self.roles}
        return SimpleNamespace(content=json.dumps(payload))


class _BoomLLM:
    def chat_completion(self, messages, **kwargs):
        raise RuntimeError("llm down")


def _service(db_session, llm) -> DiscoverService:
    return DiscoverService(db_session, llm=llm)


def test_role_judgement_updates_candidates(db_session) -> None:
    cands = [
        _candidate("Paper A on X"),
        _candidate("Paper B on Y"),
        _candidate("Paper C on Z"),
    ]
    fake = _FakeLLM(
        [
            {"index": 0, "role": "similar", "confidence": 0.8},
            {"index": 1, "role": "qualifies", "confidence": 0.7},
            {"index": 2, "role": "contradicts", "confidence": 0.9},
        ]
    )
    service = _service(db_session, fake)
    run = MagicMock(spec=DiscoverRun)

    service._judge_external_roles(run, "research question", cands)

    assert cands[0].role == "similar"
    assert cands[1].role == "qualifies"
    assert cands[2].role == "contradicts"
    assert cands[0].role_confidence == 0.8
# 整个批次只调用一次 LLM（3 个候选 <= batch_size 8）。
    assert len(fake.calls) == 1


def test_role_judgement_batches_large_sets(db_session) -> None:
    cands = [_candidate(f"Paper {i}") for i in range(12)]
    fake = _FakeLLM(
        [{"index": i, "role": "similar", "confidence": 0.6} for i in range(8)]
    )
    service = _service(db_session, fake)
    service._judge_external_roles(MagicMock(spec=DiscoverRun), "q", cands)
# 12 个候选 / batch 8 -> 2 次调用；第二批没有返回有效内容，
# 因此这些候选保留 heuristic role。
    assert len(fake.calls) == 2


def test_role_judgement_failure_keeps_heuristic(db_session) -> None:
    cands = [_candidate("Paper A")]
    service = _service(db_session, _BoomLLM())
    service._judge_external_roles(MagicMock(spec=DiscoverRun), "q", cands)
# LLM 失败 -> 保留 heuristic role。
    assert cands[0].role == "similar"
    assert cands[0].role_confidence == 0.35


def test_role_judgement_bad_shape_keeps_heuristic(db_session) -> None:
    cands = [_candidate("Paper A")]
    fake = _FakeLLM([])  # empty roles array
    service = _service(db_session, fake)
    service._judge_external_roles(MagicMock(spec=DiscoverRun), "q", cands)
    assert cands[0].role == "similar"


def test_role_map_normalizes_variants(db_session) -> None:
    cands = [_candidate("Paper A")]
    fake = _FakeLLM([{"index": 0, "role": "qualify", "confidence": 0.7}])  # "qualify" not "qualifies"
    service = _service(db_session, fake)
    service._judge_external_roles(MagicMock(spec=DiscoverRun), "q", cands)
    assert cands[0].role == "qualifies"


def test_role_judgement_out_of_range_index_ignored(db_session) -> None:
    cands = [_candidate("Paper A")]
    fake = _FakeLLM([{"index": 5, "role": "contradicts", "confidence": 0.9}])
    service = _service(db_session, fake)
    service._judge_external_roles(MagicMock(spec=DiscoverRun), "q", cands)
    assert cands[0].role == "similar"  # unchanged, index out of range
