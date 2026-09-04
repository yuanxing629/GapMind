# GraphRAG shadow 评测

This directory is for Phase 0/1 observation replay. It deliberately does not
create or modify a fixed Gold Set.

Keep the data roles separate:

- `gold/`: manually curated questions and expected evidence identities only;
- `observations/`: exported dense and GraphRAG shadow audits;
- `verdicts/`: human review, including `human_verdict` and notes.

An observation record can be either a persisted Chat response or a bare
`retrieval_audit` object. The comparator checks GraphRAG path integrity,
workspace isolation, and fallback/truncation diagnostics; it does not turn
mechanical counts into an answer-quality gate.

Example:

```powershell
cd D:\MyCode\Spark-competition\refactor\GapMind
backend\.venv\Scripts\python.exe evaluation\graphrag\compare_shadow.py `
  --dense evaluation\graphrag\observations\dense.json `
  --shadow evaluation\graphrag\observations\shadow.json
```

The real Workspace sample must contain single-paper, cross-paper, multi-hop,
claim/limitation, evidence-insufficient, and isolation cases. Do not label a
draft observation as confirmed Gold, and leave `human_verdict` blank until a
person has reviewed the source evidence.
