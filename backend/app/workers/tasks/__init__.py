"""Celery 任务包。

Phase 0：空包。Phase 2 将加入以下任务：
- parse_pdf：PDF -> 文本 + 分块
- embed_chunks：分块 -> Milvus 向量
- extract_knowledge：分块 -> KnowledgeItem + EvidenceSpan
"""
