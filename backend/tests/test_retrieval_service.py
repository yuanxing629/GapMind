"""检索流水线单元测试。"""

from __future__ import annotations

from app.domains.retrieval import service
from app.domains.retrieval.schemas import RetrievalResultItem
from app.gateway.judge import JudgementHit, JudgementResult


def test_judge_items_processes_every_result_in_batches(monkeypatch) -> None:
    batch_sizes: list[int] = []

    class FakeJudge:
        def judge_batch(
            self,
            claim: str,
            passages: list[str],
            *,
            max_passages: int,
        ) -> JudgementResult:
            batch_sizes.append(len(passages))
            assert max_passages == len(passages)
            return JudgementResult(
                hits=[
                    JudgementHit(
                        index=index,
                        judgement="supports",
                        confidence=0.8,
                    )
                    for index in range(len(passages))
                ]
            )

    monkeypatch.setattr(service, "get_judgement_gateway", FakeJudge)
    items = [
        RetrievalResultItem(text=f"passage {index}")
        for index in range(10)
    ]

    judged = service._judge_items("claim", items)

    assert batch_sizes == [8, 2]
    assert all(item.retrieval_stage == "llm_judged" for item in judged)
    assert all(item.judgement == "supports" for item in judged)
    assert all(item.judgement_confidence == 0.8 for item in judged)
