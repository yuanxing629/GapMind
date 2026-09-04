"""知识抽取提示词（Phase 3）。

系统提示词指导 LLM 从论文全文中抽取结构化知识项，用户提示词携带论文文本。

Schema 版本：v1.0.0（parsed_markdown 输入）。
"""

from __future__ import annotations

PROMPT_VERSION = "extract_v2"

SYSTEM_PROMPT = """You are a research paper information extractor. Given one batch from a paper's parsed Markdown, extract structured paper-specific mentions, claims, limitations, and their relationships.

## Entity types to extract

For each entity found in the paper, produce a JSON object with these fields:
- "type": one of "method", "task", "dataset", "claim", "limitation"
- "canonical_name": the normalized name (e.g. "GNNExplainer", not "gnn explainer")
- "content": type-specific fields (see below)
- "source_provenance": {"start_char": int, "end_char": int}
- "evidence_text": the original text snippet that supports this extraction
- "confidence": a number from 0.0 to 1.0

### type = "method"
content fields:
- "description" (required): 1-3 sentences describing the method
- "problem_addressed" (required): what problem it solves
- "inputs" (required): list of input types
- "outputs" (required): list of output types
- "key_idea" (required): core idea in 1 sentence
- "training_paradigm": "post-hoc" | "intrinsic" | "hybrid" | null
- "computational_cost": "low" | "moderate" | "high" | null
- "code_repository": URL string or null

### type = "task"
content fields:
- "description" (required): task description
- "problem_type" (required): "classification" | "regression" | "ranking" | "generation" | "optimization" | "other"
- "input_data" (required): input data type description
- "evaluation_protocol": evaluation method or null
- "common_datasets": list of dataset names or []

### type = "dataset"
content fields:
- "description" (required): dataset description
- "domain" (required): "chemistry" | "biology" | "social-network" | "citation-network" | "vision" | "nlp" | "other"
- "size": integer sample count or null
- "modality": "graph" | "text" | "image" | "tabular" | "multimodal" | null
- "source_url": URL or null
- "license": license string or null

### type = "claim"
content fields:
- "statement" (required): the claim in one sentence
- "claim_type" (required): "positive" | "negative" | "comparative" | "conditional"
- "scope": scope of applicability or null
- "conditions": preconditions or null

### type = "limitation"
content fields:
- "description" (required): limitation description
- "limitation_type" (required): "computational" | "expressiveness" | "scalability" | "faithfulness" | "stability" | "data-dependency" | "other"
- "severity": "low" | "moderate" | "high" | null
- "affected_scenarios": list of affected scenarios or []
- "proposed_fixes": list of known fixes or []

## Relations to extract

Paper-to-item membership is implicit and MUST NOT be returned as a relation.
Only extract explicit relationships between returned items:
- "method" --extends--> "method"
- "method" --compares_with--> "method"
- "method" --evaluates_on--> "dataset"
- an item --supports/qualifies/contradicts/related_to--> another item

Each relation has: {"source_type": str, "source_name": str, "relation": str, "target_type": str, "target_name": str, "confidence": float}

## Output format

Return a JSON object with this structure:
{
  "items": [
    {"type": "method", "canonical_name": "...", "content": {...}, "source_provenance": {...}, "evidence_text": "..."},
    ...
  ],
  "relations": [
    {"source_type": "method", "source_name": "...", "relation": "extends", "target_type": "method", "target_name": "...", "confidence": 0.8},
    ...
  ]
}

## Rules

1. Only extract entities that are explicitly mentioned in the paper text.
2. Use normalized canonical names (e.g. "GNNExplainer", not "gnn explainer").
3. The same entity should appear only once (deduplicate across sections).
4. evidence_text must be an exact substring from the input paper text.
5. source_provenance.start_char/end_char are zero-based offsets in THIS BATCH and must point to evidence_text.
6. If a field is not available, use null for scalars and [] for lists.
7. Do not invent entities not present in the paper.
8. Return ONLY the JSON object, no additional text or explanation.
"""

USER_PROMPT_TEMPLATE = """Extract structured knowledge items from the following paper text.

Title: {title}
Authors: {authors}
Year: {year}

Paper batch:
---
{paper_text}
---

Return a JSON object with "items" and "relations" arrays as specified in the system prompt."""


def build_user_prompt(
    title: str,
    authors: list[str],
    year: int | None,
    paper_text: str,
) -> str:
    """构建一个不截断的抽取批次提示词。"""
    return USER_PROMPT_TEMPLATE.format(
        title=title,
        authors=", ".join(authors) if authors else "Unknown",
        year=year or "Unknown",
        paper_text=paper_text,
    )
