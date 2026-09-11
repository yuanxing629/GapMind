"""知识抽取提示词的结构约束测试。"""

from __future__ import annotations

import json

from app.gateway.prompts.extract_v1 import SYSTEM_PROMPT


def test_extraction_prompt_contains_list_field_few_shot_examples() -> None:
    """提示词应明确指导模型把 inputs 和 outputs 返回为数组。"""
    assert '"inputs": ["A trained GNN", "A class label"]' in SYSTEM_PROMPT
    assert '"outputs": ["A representative graph pattern"]' in SYSTEM_PROMPT
    assert "method.content.inputs and method.content.outputs" in SYSTEM_PROMPT
    assert "one value and [] when unavailable" in SYSTEM_PROMPT
    assert "at most 12 items and at most 12 relations" in SYSTEM_PROMPT


def test_extraction_prompt_few_shot_json_is_parseable() -> None:
    """few-shot 示例中的 JSON 对象应保持可解析，避免提示词自身损坏。"""
    blocks = SYSTEM_PROMPT.split("Correct output:\n")[1:]
    blocks[0] = blocks[0].split("\n\nExample 2", 1)[0]
    blocks[1] = blocks[1].split("\n\n## Rules", 1)[0]

    assert len(blocks) == 2
    for block in blocks:
        parsed = json.loads(block)
        assert isinstance(parsed["items"], list)
        assert isinstance(parsed["relations"], list)
        for item in parsed["items"]:
            assert item["evidence_text"]
            assert item["source_provenance"]["end_char"] > item["source_provenance"]["start_char"]
