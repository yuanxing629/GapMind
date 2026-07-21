# GapMind 数据契约（Data Contracts）

> 本文档定义 GapMind 三人协作中所有跨人数据交付的格式和接口。
> 所有队友必须严格遵守本契约，**任何字段变更必须先在本文档更新并通知相关方**。
> 最后更新：2026-07-19
> 版本：v0.1（Phase 2 准备阶段）

## 为什么要这份文档

GapMind 是一个完整系统，不是三个独立项目。三人协作模式：

```
yx（项目核心）
   ├── 基础设施（Workspace/Paper/Task/Timeline/Knowledge 数据层 + API）
   ├── PDF 解析 Pipeline（产出 chunks）
   │        ↓ Contract #1: chunks
   ├── zwx（RAG）
   │   ├── 消费 chunks 建索引
   │   └── 产出 RetrievalResult
   │        ↓ Contract #4: RetrievalResult
   ├── Knowledge 抽取 Pipeline
   │   ├── 基线：Deepseek 直接抽取
   │   ├── 输入：chunks
   │   └── 输出：KnowledgeItem + EvidenceSpan
   │        ↓ Contract #2: KnowledgeItem（含 content schema）
   ├── zf（微调）
   │   ├── 消费 (chunks, KnowledgeItem) 构造训练样本
   │   │   ↓ Contract #3: 训练样本格式
   │   ├── 产出 LoRA adapter
   │   └── 通过 Model Gateway 接入，替代/补充 Deepseek
   └── Discover Agent（消费 KnowledgeItem + RetrievalResult）
```

**核心原则**：
1. 所有数据用 JSON / JSONL 格式（方便 Python 消费、可 diff）
2. 所有 ID 是 UUID 字符串（36 字符，与 PostgreSQL 一致）
3. 所有时间戳是 ISO 8601 UTC（如 `2026-07-19T10:00:00Z`）
4. 文本字段用 UTF-8 编码
5. 字段命名用 `snake_case`

---

## 契约总览

| # | 契约 | 生产者 | 消费者 | 格式 | 状态 |
|---|------|--------|--------|------|------|
| 1 | `chunks` | yx（Phase 2） | zwx（RAG） | JSONL 文件 + Milvus | 🚧 待实现 |
| 2 | `knowledge_items`（含 content schema） | yx（Phase 3）+ zf（微调模型） | Discover Agent | PostgreSQL `knowledge_items` 表 | 🚧 待实现 |
| 3 | `training_samples` | zf（构造） | zf（微调训练） | JSONL 文件 | 🚧 待实现 |
| 4 | `RetrievalResult` | zwx（RAG） | yx的 Discover Agent | Python dataclass / JSON | 🚧 待实现 |
| 5 | `opportunity_candidates` | yx（Discover Agent） | zf（Opportunity 微调） | JSONL 文件 | 🚧 待实现 |
| 6 | `LLM provider 接口` | zf（部署微调模型） | yx的 Model Gateway | OpenAI 兼容 HTTP | 🚧 待实现 |

---

## Contract #1: `chunks`

**生产者**：yx（Phase 2 PDF 解析 pipeline）
**消费者**：zwx（RAG 子系统，建 Milvus 索引）
**传输方式**：JSONL 文件 + 同步写入 Milvus
**何时交付**：Phase 2 完成后，每周增量交付新解析的 chunks

### 1.1 单条 chunk 格式（JSONL，一行一条）

```json
{
  "chunk_id": "550e8400-e29b-41d4-a716-446655440000",
  "workspace_id": "04f801d6-7473-4a8b-915f-60448a10d748",
  "paper_id": "12345678-1234-1234-1234-123456789012",
  "artifact_id": "87654321-4321-4321-4321-210987654321",
  "chunk_index": 5,
  "section": "Method",
  "subsection": "3.2 GNNExplainer Formulation",
  "text": "In this work, we formulate the explanation of GNN predictions as the following optimization problem...",
  "start_char": 12340,
  "end_char": 12890,
  "page_start": 3,
  "page_end": 3,
  "tokens_estimate": 480,
  "chunk_version": "v1",
  "created_at": "2026-07-19T10:00:00Z"
}
```

### 1.2 字段规范

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `chunk_id` | string(UUID) | ✅ | 全局唯一，UUID v4 |
| `workspace_id` | string(UUID) | ✅ | 所属 workspace |
| `paper_id` | string(UUID) | ✅ | 所属 paper |
| `artifact_id` | string(UUID) | ✅ | 来自哪个 parsed_text artifact |
| `chunk_index` | int | ✅ | 在论文内的顺序（0-based） |
| `section` | string | ❌ | 章节名：`Abstract` / `Introduction` / `Method` / `Experiment` / `Conclusion` / `Related Work` / `Discussion` / `Unknown` |
| `subsection` | string | ❌ | 子章节标题（如 "3.2 GNNExplainer Formulation"） |
| `text` | string | ✅ | chunk 文本，UTF-8，**已清洗**（去多余空白、修复 PDF 提取的常见错误） |
| `start_char` | int | ✅ | 在 parsed_text 中的起始字符偏移 |
| `end_char` | int | ✅ | 在 parsed_text 中的结束字符偏移 |
| `page_start` | int | ❌ | 起始页码（1-based） |
| `page_end` | int | ❌ | 结束页码（跨页 chunk） |
| `tokens_estimate` | int | ✅ | 估算 token 数（用 tiktoken cl100k_base 或 BGE-m3 tokenizer） |
| `chunk_version` | string | ✅ | 分块策略版本，如 `v1`（分块算法变更时递增） |
| `created_at` | string(ISO 8601) | ✅ | 创建时间 |

### 1.3 分块策略（生产者必须遵守）

- **目标大小**：512 tokens ± 50（BGE-m3 支持 8192 上下文，但小 chunk 检索更精准）
- **重叠**：相邻 chunk 重叠 50 tokens（避免切断关键语义）
- **切分优先级**：
  1. 先按章节切（用 PyMuPDF heading 检测 + 启发式）
  2. 章节过长时按段落切（双换行符）
  3. 段落过长时按句子切（用 NLP 句子分割器）
- **最小 chunk**：不小于 100 tokens（太碎的 chunk 合并到相邻 chunk）
- **最大 chunk**：不超过 800 tokens（超过则强制切分）

**为什么重要**：zwx 的检索质量直接受 chunk 大小影响。统一分块策略让 RAG 评估可复现。

### 1.4 文本清洗规范

`text` 字段必须经过以下清洗：
- ✅ 合并被 PDF 提取破坏的断行（如 `opti-\nmization` → `optimization`）
- ✅ 合并被双栏布局错切的句子
- ✅ 去除页眉页脚（如固定的 "Proceedings of..."）
- ✅ 去除参考文献编号（如 `[12]`）
- ✅ 标准化空白字符（多个空格→一个，制表符→空格）
- ❌ 不修改原文措辞（保持学术准确性）
- ❌ 不翻译（保持原文语言）

### 1.5 Milvus collection schema（zwx 建索引）

zwx 根据 chunks 创建 Milvus collection：

```python
# Collection name: {prefix}paper_chunks（prefix 从配置读）
# 如 gapmind_paper_chunks

fields = [
    FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=36, is_primary=True),
    FieldSchema(name="workspace_id", dtype=DataType.VARCHAR, max_length=36),
    FieldSchema(name="paper_id", dtype=DataType.VARCHAR, max_length=36),
    FieldSchema(name="chunk_index", dtype=DataType.INT64),
    FieldSchema(name="section", dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=4096),
    FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1024),  # BGE-m3
]

index_params = {
    "index_type": "HNSW",  # 或 IVF_FLAT for larger scale
    "metric_type": "COSINE",  # BGE-m3 推荐 cosine
    "params": {"M": 16, "efConstruction": 200},
}
```

**重要约定**：
- Embedding 模型固定用 `BAAI/bge-m3`（1024 维）——**换模型必须用新 collection 名**
- 度量固定用 `COSINE`
- zwx 如要做"换模型对比实验"，新建 `paper_chunks_xxx` collection，不要覆盖原 collection

### 1.6 交付方式

- **增量交付**：yx每解析完一篇论文，把该论文的所有 chunks 追加到 JSONL 文件
- **文件路径**：`data/chunks/{workspace_id}/{paper_id}.jsonl`（每篇论文一个文件）
- **同步 Milvus**：yx解析完后调用zwx 提供的 `index_chunks(paper_id)` 函数，让 Milvus 自动更新
- **验证**：交付前用 `python scripts/validate_chunks.py <file>` 校验格式

---

## Contract #2: `knowledge_items`（含 content schema）

**生产者**：
- yx（Phase 3，用 Deepseek 跑基线抽取）
- zf（Phase 3 后期，用微调模型替代 Deepseek）

**消费者**：
- Discover Agent（Phase 5）
- 前端 Knowledge 列表页
- zf（作为微调训练的 ground truth 目标）

**传输方式**：写入 PostgreSQL `knowledge_items` 表 + JSONL 导出供zf 训练
**何时交付**：Phase 3 开始后持续产出

### 2.1 KnowledgeItem 基础结构

（与 [db_schema.md](db_schema.md) 的 `knowledge_items` 表一致）

```json
{
  "id": "uuid",
  "workspace_id": "uuid",
  "type": "method",
  "canonical_name": "GNNExplainer",
  "content": { /* 见下方 type-specific schema */ },
  "source_provenance": {
    "paper_id": "uuid",
    "parsed_text_artifact_id": "uuid",
    "start_char": 1234,
    "end_char": 1456,
    "extracted_by": "deepseek-v4-flash",
    "extraction_run_id": "uuid"
  },
  "created_by": "agent",
  "confidence": 0.85,
  "status": "extracted_candidate",
  "version": 1,
  "is_deleted": false,
  "created_at": "2026-07-19T10:00:00Z",
  "updated_at": "2026-07-19T10:00:00Z"
}
```

**关于 `source_provenance` 字段的设计说明**（重要）：
- **`parsed_text_artifact_id` + `start_char` + `end_char`**：证据定位到 `parsed_text`（全文）的字符偏移。抽取时 LLM 的输入是 `parsed_text`（整篇或章节级），不是 chunks。证据精确定位回全文，而不是定位回 chunk。
- **不再有 `chunk_id` 字段**：chunks 是给 RAG 检索用的（Contract #1），不是给抽取用的。evidence_spans 和 chunks 解耦——evidence 指向全文，chunks 指向向量索引。
- 如果某个 evidence 恰好落在一个 chunk 范围内，检索时可以通过 char offset 计算 chunk 关联（这是检索层的事，不是抽取层的事）。

### 2.2 Phase 3 支持的 5 种类型及其 `content` schema

**这是最关键的契约**——zf 的微调模型必须输出严格符合这些 schema 的 JSON。

#### type = "method"

```json
{
  "description": "GNNExplainer formulates explanation as max mutual information between subgraph and prediction.",
  "problem_addressed": "Why does the GNN make this prediction?",
  "inputs": ["graph", "node index", "trained GNN model"],
  "outputs": ["explanation subgraph", "feature importance"],
  "key_idea": "Optimize a graph mask to retain maximal mutual information.",
  "training_paradigm": "post-hoc",
  "computational_cost": "moderate",
  "code_repository": "https://github.com/RexYing/gnn-model-explainer"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `description` | string | ✅ | 方法核心描述（1-3 句） |
| `problem_addressed` | string | ✅ | 解决什么问题 |
| `inputs` | string[] | ✅ | 输入是什么 |
| `outputs` | string[] | ✅ | 输出是什么 |
| `key_idea` | string | ✅ | 核心思想（1 句话） |
| `training_paradigm` | string | ❌ | `post-hoc` / `intrinsic` / `hybrid` |
| `computational_cost` | string | ❌ | `low` / `moderate` / `high` |
| `code_repository` | string(URL) | ❌ | GitHub 链接（如有） |

#### type = "task"

```json
{
  "description": "Graph classification aims to predict a label for an entire graph.",
  "problem_type": "classification",
  "input_data": "graph-structured data",
  "evaluation_protocol": "10-fold cross validation",
  "common_datasets": ["MUTAG", "PROTEINS", "IMDB-BINARY"]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `description` | string | ✅ | 任务描述 |
| `problem_type` | string | ✅ | `classification` / `regression` / `ranking` / `generation` / `optimization` / `other` |
| `input_data` | string | ✅ | 输入数据类型 |
| `evaluation_protocol` | string | ❌ | 评估方法 |
| `common_datasets` | string[] | ❌ | 常用数据集（与 dataset 类型 KItem 的 canonical_name 对齐） |

#### type = "dataset"

```json
{
  "description": "MUTAG is a collection of nitro compounds, labeled mutagenic/non-mutagenic.",
  "domain": "chemistry",
  "size": 188,
  "modality": "graph",
  "source_url": "https://ls11-www.cs.tu-dortmund.de/people/mutag",
  "license": "academic-only"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `description` | string | ✅ | 数据集描述 |
| `domain` | string | ✅ | `chemistry` / `biology` / `social-network` / `citation-network` / `vision` / `nlp` / `other` |
| `size` | int | ❌ | 样本数量 |
| `modality` | string | ❌ | `graph` / `text` / `image` / `tabular` / `multimodal` |
| `source_url` | string(URL) | ❌ | 下载链接 |
| `license` | string | ❌ | 许可证 |

#### type = "claim"

```json
{
  "statement": "GNNExplainer provides faithful explanations for GNN predictions.",
  "claim_type": "positive",
  "scope": "small molecular graphs",
  "conditions": "when the GNN is sufficiently expressive"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `statement` | string | ✅ | claim 内容（一句话陈述） |
| `claim_type` | string | ✅ | `positive` / `negative` / `comparative` / `conditional` |
| `scope` | string | ❌ | 适用范围 |
| `conditions` | string | ❌ | 前提条件 |

> **注**：`supporting_evidence` / `contradicting_evidence` 不在 content 里——证据关系通过 `evidence_spans` 表独立存储（见 2.3 节），`evidence_spans.relation` 字段区分 supports/qualifies/contradicts。这样 claim content 保持简洁，证据关系可以独立演化。

#### type = "limitation"

```json
{
  "description": "GNNExplainer requires re-optimization for each instance, making it slow on large graphs.",
  "limitation_type": "computational",
  "severity": "moderate",
  "affected_scenarios": ["large graphs", "real-time applications"],
  "proposed_fixes": ["PGExplainer: amortize optimization across instances"]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `description` | string | ✅ | 局限性描述 |
| `limitation_type` | string | ✅ | `computational` / `expressiveness` / `scalability` / `faithfulness` / `stability` / `data-dependency` / `other` |
| `severity` | string | ❌ | `low` / `moderate` / `high` |
| `affected_scenarios` | string[] | ❌ | 受影响的场景 |
| `proposed_fixes` | string[] | ❌ | 后续论文提出的修复（如已知） |

### 2.3 EvidenceSpan 配套结构

每个 KnowledgeItem 应该至少有一个 EvidenceSpan 指向支持它的论文文本。EvidenceSpan 与 [db_schema.md](db_schema.md) 的 `evidence_spans` 表一致：

```json
{
  "id": "uuid",
  "workspace_id": "uuid",
  "knowledge_item_id": "uuid",          // 指向所属 KItem
  "paper_id": "uuid",
  "artifact_id": "uuid",                // 指向 parsed_text artifact（全文）
  "start_char": 12340,                  // 在 parsed_text 中的字符偏移
  "end_char": 12890,
  "text": "We propose GNNExplainer...", // 证据原文
  "relation": "supports",               // supports / qualifies / contradicts
  "confidence": 0.92
}
```

**关键约定**：
- **`artifact_id` 指向 `parsed_text` artifact**（不是 chunk_index artifact）。证据定位回全文，不是定位回 chunk。
- **`start_char` / `end_char` 是在 `parsed_text` 中的字符偏移**，和 Contract #2 的 `source_provenance.start_char` / `end_char` 含义一致。
- **不再有 `chunk_id` / `chunk_index` 字段**：chunks 是检索层（zwx 的工作）的概念，不是抽取层的概念。evidence 和 chunks 解耦。
- 如果检索时需要知道某个 evidence 落在哪个 chunk，可以通过 `start_char` 在 chunks JSONL 里二分查找（chunk 的 `start_char`/`end_char` 是已知边界）。这是检索层的事，不是抽取层的事。
- Discover Agent 的溯源链路：KnowledgeItem → EvidenceSpan → parsed_text → paper，**不经过 chunks**。chunks 只用于"找相似内容"的向量检索。

### 2.4 状态流转

所有 KnowledgeItem 初始状态为 `extracted_candidate`：

```
extracted_candidate  ← agent 抽取产出
       ↓
evidence_backed_proposal  ← 经过 evidence 检索验证（Phase 5 Discover）
       ↓
human_confirmed  ← 用户在 UI 确认（Phase 4 HITL）
       ↓
experiment_validated  ← 实验验证（P1，Phase 7）

任何阶段 → rejected / deprecated / invalidated
```

zf 的微调模型只产出 `extracted_candidate` 状态，不跳过任何状态。

### 2.5 `created_by` 字段约定

| 值 | 含义 | 谁产出 |
|----|------|--------|
| `system` | 系统自动（如 PDF metadata 提取） | yx的 code |
| `agent` | LLM agent 抽取 | yx（Deepseek）或zf（微调模型） |
| `user` | 用户手动创建/编辑 | 前端用户 |

zf 的微调模型产出标记为 `agent`，`source_provenance.extracted_by` 写明模型名（如 `qwen2.5-7b-lora-extract-v1`）。

---

## Contract #3: `training_samples`（微调训练数据）

**生产者**：zf（基于yx产出的 **parsed_text** + KnowledgeItems 构造）
**消费者**：zf（LoRA 微调训练）
**传输方式**：JSONL 文件
**何时使用**：Phase 3 中后期

### 3.1 抽取任务训练样本格式（ShareGPT / Alpaca 兼容）

**重要设计**：LLM 微调训练样本的 input 是 `parsed_text`（论文全文或章节级 text），**不是 chunks**。Chunks 是给 RAG 检索用的，不是给抽取用的。

```jsonl
{
  "messages": [
    {
      "role": "system",
      "content": "You are a research paper information extractor. Given a paper's parsed text, extract structured knowledge items (Methods, Tasks, Datasets, Claims, Limitations) and their relationships. For each knowledge item extracted, return it under the appropriate type with full content (description, problem_addressed, inputs/outputs, etc.). For each item, also return the originating paper and the evidence_span pointer to the paper text. Return a JSON object with a 'items' array. Each item must have 'type', 'canonical_name', 'content', 'source_provenance', and 'evidence_text' fields."
    },
    {
      "role": "user",
      "content": "From the following paper text, extract structured knowledge items:\n\nTitle: GNNExplainer: Generating Explanations for Graph Neural Networks\n\n<parsed_text_or_section_text_here>"
    },
    {
      "role": "assistant",
      "content": "{\"items\": [{\"type\": \"method\", \"canonical_name\": \"GNNExplainer\", \"content\": {\"description\": \"GNNExplainer formulates explanation as max mutual information between subgraph and prediction.\", \"problem_addressed\": \"Why does the GNN make this prediction?\", \"inputs\": [\"graph\", \"node index\", \"trained GNN model\"], \"outputs\": [\"explanation subgraph\"], \"key_idea\": \"Optimize a graph mask to retain maximal mutual information.\", \"training_paradigm\": \"post-hoc\", \"computational_cost\": \"moderate\", \"code_repository\": \"https://github.com/RexYing/gnn-model-explainer\"}, \"source_provenance\": {\"paper_id\": \"uuid\", \"start_char\": 1234, \"end_char\": 1456}, \"evidence_text\": \"We formulate GNN explanation as the following optimization problem...\"}, {\"type\": \"task\", \"canonical_name\": \"node classification explanation\", \"content\": {...}, \"source_provenance\": {...}, \"evidence_text\": \"...\"}]}"
    }
  ],
  "metadata": {
    "parsed_text_artifact_id": "uuid",
    "section_name": "method",
    "paper_id": "uuid",
    "workspace_id": "uuid",
    "annotator": "teammate_b",
    "annotation_quality": "gold",
    "schema_version": "v0.2"
  }
}
```

### 3.2 字段规范

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `messages` | object[] | ✅ | OpenAI chat 格式：system / user / assistant |
| `messages[*].role` | string | ✅ | `system` / `user` / `assistant` |
| `messages[*].content` | string | ✅ | 消息内容 |
| `metadata` | object | ✅ | 训练样本元信息（不参与训练，用于追溯） |
| `metadata.parsed_text_artifact_id` | string(UUID) | ✅ | 训练样本来自哪篇论文的 parsed_text |
| `metadata.section_name` | string | ❌ | 如果是章节级样本，标注章节名（如 "method", "experiment"） |
| `metadata.paper_id` | string(UUID) | ✅ | 哪篇论文 |
| `metadata.workspace_id` | string(UUID) | ✅ | |
| `metadata.annotator` | string | ✅ | 标注者：`teammate_b` / `deepseek_v4_flash` / `you` / `advisor` |
| `metadata.annotation_quality` | string | ✅ | `gold`（人工修正）/ `silver`（LLM 生成未修正）/ `bronze`（LLM 生成 + 抽查） |
| `metadata.schema_version` | string | ✅ | 本契约版本，如 `v0.2` |

### 3.3 assistant 输出的 JSON 规范

assistant 的 `content` 必须是合法 JSON 字符串，解析后符合：

```json
{
  "items": [
    {
      "type": "method" | "task" | "dataset" | "claim" | "limitation",
      "canonical_name": "string",
      "content": { /* Contract #2 中对应 type 的 content schema */ },
      "source_provenance": {
        "paper_id": "uuid",
        "start_char": int,            // 在 parsed_text 中的字符偏移
        "end_char": int
      },
      "evidence_text": "直接引用的原文片段"
    }
  ]
}
```

**关键约束**（zf 必须保证训练数据符合）：
- ✅ `items` 可以为空数组（论文没有可抽取内容时，如纯实验报告）
- ✅ 每个 item 的 `source_provenance.start_char`/`end_char` 必须在 `parsed_text` 中精确可定位
- ✅ 每个 item 的 `evidence_text` 必须是从原 `parsed_text` 截取的真实片段
- ✅ `canonical_name` 用规范名称（如 `GNNExplainer` 而非 `gnn explainer` 或 `GNN-Explainer`）
- ✅ 同一篇论文内同一实体只抽一次（即使在不同章节出现）
- ❌ 不抽取论文中没有的内容（不允许"基于常识补充"）
- ❌ 不跨越论文边界抽取（一个样本对应一篇论文）

**与上一版的关键变更**（zf 反馈驱动）：
- ✅ 输入：parsed_text（全文或章节级）→ 取代 chunk
- ✅ evidence：从 chunk 内偏移 → 改为 parsed_text 内偏移
- ✅ 输出范围：包含 items 数组（保留），但抽取粒度从"单 chunk 的局部信息"变为"整篇论文的全局实体和关系"

### 3.4 数据集划分

zf 构造的训练数据必须按以下比例划分：

| 划分 | 比例 | 用途 |
|------|------|------|
| `train` | 80% | LoRA 训练 |
| `val` | 10% | 训练中验证 + 早停 |
| `test` | 10% | 最终评估，**训练过程中绝不接触** |

**划分原则**：
- 按 **paper_id** 划分，不按 chunk（避免同一论文的 chunks 同时在 train 和 test，导致泄漏）
- 划分后记录在 `training_samples_split.json`：
  ```json
  {
    "train_papers": ["uuid1", "uuid2", ...],
    "val_papers": ["uuid3", ...],
    "test_papers": ["uuid4", ...],
    "split_version": "v1",
    "split_at": "2026-07-19"
  }
  ```

### 3.5 数据量要求

| 数据集 | 最少样本数 | 用途 |
|--------|-----------|------|
| Smoke Set（开发期） | 100-200 | 快速迭代 prompt / 验证 pipeline（来自 8-12 篇论文） |
| Development Set（正式训练） | 1000-3000 | LoRA 微调（Qwen3-8B 经验值，来自 30-40 篇论文） |
| 评估集（固定） | 200-500 | 不参与训练，用于报告指标 |

**构造策略**（yx + zf 协作）：
1. **Silver 阶段**（Week 1-2）：yx用 Deepseek 跑 30-40 篇论文，产出 ~1500-2000 个 silver 样本，不人工修正
2. **Gold 阶段**（Week 3-4）：yx和zf 从 silver 中抽 300-500 个修正为 gold，作为评估集 + 高质量训练样本
3. **混合训练**：用 silver + gold 一起训练，gold 作为评估

> **关于 `chunk_id` 的使用约定**（避免混淆）：
> - **Contract #1 `chunks`**：`chunk_id` 是 chunks JSONL 里的 UUID（检索用的向量单位）
> - **Contract #4 `RetrievalResult`**：`chunk_id` 引用 Contract #1（RAG 检索返回的是 chunk 级结果）
> - **Contract #2 `knowledge_items` 的 `source_provenance`**：**不包含 `chunk_id`**，而是 `parsed_text_artifact_id` + char offset（证据定位回全文，不是 chunk）
> - **Contract #3 `training_samples` 的 `metadata`**：**不包含 `chunk_id`**，而是 `parsed_text_artifact_id`（训练输入是 parsed_text，不是 chunk）

---

## Contract #4: `RetrievalResult`

**生产者**：zwx（RAG 子系统）
**消费者**：yx的 Discover Agent（Phase 5）+ 前端检索 UI
**传输方式**：Python 函数返回值（同进程调用，不暴露 HTTP）
**何时使用**：Phase 3 开始

### 4.1 Python 接口

zwx 在 `backend/app/domains/retrieval/service.py` 实现：

```python
from dataclasses import dataclass

@dataclass
class RetrievalResult:
    """单个检索结果。"""
    chunk_id: str
    paper_id: str
    paper_title: str
    paper_year: int | None
    chunk_index: int
    section: str | None
    text: str
    score: float  # 0.0 - 1.0，越高越相关
    retrieval_method: str  # "semantic" / "similar_work" / "counter_evidence"


@dataclass
class RetrievalResponse:
    """一次检索的完整响应。"""
    query: str
    items: list[RetrievalResult]
    total: int
    latency_ms: float
    filters_applied: dict


def semantic_search(
    workspace_id: str,
    query: str,
    top_k: int = 10,
    filters: dict | None = None,
) -> RetrievalResponse:
    """语义检索 chunks。
    
    Args:
        workspace_id: 限定在哪个 workspace 检索
        query: 自然语言查询
        top_k: 返回数量
        filters: 可选过滤，如 {"paper_ids": [...], "sections": ["Method"], "min_score": 0.5}
    """
    pass


def find_similar_work(
    workspace_id: str,
    paper_id: str,
    top_k: int = 10,
) -> RetrievalResponse:
    """找与指定论文相似的其他论文 chunks。
    
    实现：对该论文的所有 chunks 做 mean pooling 得到论文向量，然后检索 top_k。
    结果排除该论文自己的 chunks。
    """
    pass


def find_counter_evidence(
    workspace_id: str,
    claim_text: str,
    top_k: int = 10,
) -> RetrievalResponse:
    """找反驳/限定某 claim 的证据。
    
    实现：query 改写为"claims against: {claim_text}"或用对比 prompt，
    然后语义检索。可结合 knowledge_items 中的 contradicts 关系。
    """
    pass
```

### 4.2 JSON 序列化（用于日志/调试）

如果需要把 RetrievalResult 持久化（如记录 Discover Agent 用了哪些证据），用以下 JSON 格式：

```json
{
  "query": "graph neural network explainability",
  "items": [
    {
      "chunk_id": "uuid",
      "paper_id": "uuid",
      "paper_title": "GNNExplainer: Towards...",
      "paper_year": 2019,
      "chunk_index": 5,
      "section": "Method",
      "text": "...",
      "score": 0.87,
      "retrieval_method": "semantic"
    }
  ],
  "total": 10,
  "latency_ms": 45.2,
  "filters_applied": {"sections": ["Method"]}
}
```

### 4.3 性能契约

zwx 的检索函数必须满足：

| 指标 | 目标 |
|------|------|
| 单次检索延迟 | < 200ms（top 10） |
| 并发支持 | 至少 10 并发不降级 |
| 可用性 | 单次失败必须有 fallback（返回空列表 + 日志，不抛异常） |
| 工作区隔离 | 严格按 `workspace_id` 过滤，绝不跨 workspace 返回结果 |

**重要**：如果检索失败，zwx 的实现应该**返回空 `RetrievalResponse`**，而不是抛异常。Discover Agent 应该能优雅处理"检索不到证据"的情况（生成低 confidence 的 opportunity 或跳过）。

---

## Contract #5: `opportunity_candidates`

**生产者**：yx（Phase 5 Discover Agent）
**消费者**：zf（Opportunity 微调训练数据构造）
**传输方式**：JSONL 文件
**何时使用**：Phase 5 中期

### 5.1 单条 candidate 格式

```json
{
  "candidate_id": "uuid",
  "workspace_id": "uuid",
  "agent_run_id": "uuid",
  "title": "Why post-hoc explainers fail on heterogeneous graphs",
  "description": "Detailed description of the opportunity...",
  "evidence_refs": [
    {
      "knowledge_item_id": "uuid",
      "chunk_id": "uuid",
      "paper_id": "uuid",
      "relation": "supports",
      "text": "..."
    }
  ],
  "similar_work": [
    {
      "paper_id": "uuid",
      "paper_title": "...",
      "chunk_id": "uuid",
      "text": "...",
      "score": 0.78
    }
  ],
  "counter_evidence": [
    {
      "paper_id": "uuid",
      "paper_title": "...",
      "chunk_id": "uuid",
      "text": "...",
      "score": 0.65
    }
  ],
  "novelty_analysis": "Existing work has not addressed...",
  "feasibility_analysis": "Can be validated on datasets X, Y with compute Z.",
  "status": "candidate",
  "confidence": 0.75,
  "human_scores": {
    "evidence_grounding": 4,
    "novelty": 3,
    "feasibility": 4,
    "significance": 3,
    "actionability": 4,
    "explainability": 5
  },
  "scored_by": ["you", "teammate_b", "advisor"],
  "created_at": "2026-07-19T10:00:00Z"
}
```

### 5.2 字段规范

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `candidate_id` | string(UUID) | ✅ | candidate 唯一 ID |
| `workspace_id` | string(UUID) | ✅ | |
| `agent_run_id` | string(UUID) | ✅ | 哪次 Discover Agent run 产出 |
| `title` | string | ✅ | 机会标题 |
| `description` | string | ✅ | 详细描述 |
| `evidence_refs` | object[] | ✅ | 支持证据（至少 2 个） |
| `similar_work` | object[] | ✅ | 相似工作（来自 RetrievalResult） |
| `counter_evidence` | object[] | ✅ | 反证（来自 RetrievalResult） |
| `novelty_analysis` | string | ✅ | 新颖性分析 |
| `feasibility_analysis` | string | ✅ | 可行性分析 |
| `status` | string | ✅ | `candidate` / `confirmed` / `rejected` / `deferred` |
| `confidence` | float | ✅ | agent 自评分，0.0-1.0 |
| `human_scores` | object | ❌ | 人工评分（1-5），6 个维度 |
| `scored_by` | string[] | ❌ | 谁参与评分 |

### 5.3 人工评分维度（plans.md 定义）

zf 构造 Opportunity 微调数据时，需要yx和导师按以下维度评分：

| 维度 | 说明 | 1 分 | 5 分 |
|------|------|------|------|
| `evidence_grounding` | 证据支撑度 | 无证据，纯臆测 | 多篇论文强证据 |
| `novelty` | 新颖性 | 已有工作覆盖 | 真正新方向 |
| `feasibility` | 可行性 | 不可验证 | 1-2 个月内可验证 |
| `significance` | 重要性 | 琐碎问题 | 领域重要问题 |
| `actionability` | 可执行性 | 模糊建议 | 明确的下一步 |
| `explainability` | 可解释性 | 黑箱 | 清晰的推理链 |

### 5.4 微调数据构造（zf 负责）

zf 从 candidates 构造 DPO 或 SFT 数据：

**SFT 格式**（只用高分 candidate）：
```jsonl
{
  "messages": [
    {"role": "system", "content": "You are a research opportunity synthesizer..."},
    {"role": "user", "content": "Evidence: ...\nSimilar work: ...\nCounter-evidence: ...\n\nSynthesize a research opportunity."},
    {"role": "assistant", "content": "{high-quality opportunity JSON}"}
  ]
}
```

**DPO 格式**（用高低分对比）：
```jsonl
{
  "prompt": [{"role": "user", "content": "Evidence: ...\nSynthesize a research opportunity."}],
  "chosen": [{"role": "assistant", "content": "{high-score opportunity}"}],
  "rejected": [{"role": "assistant", "content": "{low-score opportunity}"}]
}
```

**选择建议**：
- 数据量 < 200：用 SFT（DPO 需要更多数据）
- 数据量 >= 500：优先 DPO（更稳定）
- 混合使用：先 SFT 再 DPO

---

## Contract #6: LLM provider 接口

**生产者**：zf（部署微调模型）
**消费者**：yx的 Model Gateway
**传输方式**：OpenAI 兼容 HTTP API
**何时使用**：微调模型就绪后

### 6.1 部署要求

zf 用 vLLM 或 Ollama 部署微调模型，**必须暴露 OpenAI 兼容 API**：

```
GET  /v1/models
POST /v1/chat/completions
```

### 6.2 配置约定

zf 提供以下信息给yx的 Model Gateway：

```yaml
# 在 backend/.env 增加
LOCAL_LLM_EXTRACT_BASE_URL=http://localhost:8080/v1
LOCAL_LLM_EXTRACT_MODEL=gapmind-qwen-extract-v1
LOCAL_LLM_EXTRACT_API_KEY=dummy  # vLLM 默认不校验

LOCAL_LLM_OPPORTUNITY_BASE_URL=http://localhost:8081/v1
LOCAL_LLM_OPPORTUNITY_MODEL=gapmind-qwen-opportunity-v1
LOCAL_LLM_OPPORTUNITY_API_KEY=dummy
```

### 6.3 调用契约

yx的 Model Gateway 扩展为多 provider：

```python
# app/gateway/llm.py 扩展
class LLMGateway:
    providers: dict[str, OpenAI]
    
    def chat_completion(self, messages, provider="deepseek", **kwargs):
        client = self.providers[provider]
        return client.chat.completions.create(model=..., messages=messages, **kwargs)
```

**调用约定**：
- 抽取任务：`provider="local-llm-extract"`（zf 的微调模型）
- Opportunity 生成：`provider="local-llm-opportunity"`
- 兜底 / 对比：`provider="deepseek"`
- `source_provenance.extracted_by` 字段记录用了哪个 provider

### 6.4 fallback 策略

如果本地微调模型不可用（服务挂了 / 超时）：
- **抽取任务**：自动 fallback 到 Deepseek，日志记录
- **Opportunity 生成**：自动 fallback 到 Deepseek，但 `confidence` 降权（× 0.8）

---

## 版本策略

本契约用语义化版本：

- **v0.x**：开发期，字段可能频繁变更，变更必须通知所有相关方
- **v1.0**：第一个稳定版，Phase 3 之前冻结
- **v1.x**：向后兼容的字段新增
- **v2.0**：不兼容变更（字段重命名 / 删除）

### 变更流程

任何字段变更必须：
1. 在本文档修改并标注版本号
2. 在团队群通知所有相关方
3. 更新代码中的 `schema_version` 字段
4. 已产出的数据按需迁移（提供 migration script）

---

## 依赖时间线

```
Week 1-2 (Phase 2)
  ├── yx：产出 chunks（Contract #1）
  ├── zwx：消费 chunks，建 Milvus 索引
  └── zf：消费 chunks，开始人工标注

Week 3-4 (Phase 3)
  ├── yx：产出 KnowledgeItems（Contract #2，用 Deepseek 基线）
  ├── zwx：实现 RetrievalResult（Contract #4）
  └── zf：构造训练样本（Contract #3），开始 LoRA 微调

Week 5-6 (Phase 5)
  ├── yx：产出 OpportunityCandidates（Contract #5）
  ├── zwx：优化检索 + 可选 GraphRAG
  └── zf：Opportunity 微调

Week 7+ (集成)
  ├── zf：部署微调模型（Contract #6）
  ├── yx：Model Gateway 接入多 provider
  └── 端到端测试
```

### 关键同步点

| 时间点 | 同步内容 | 参与者 |
|--------|---------|--------|
| Week 1 末 | Milvus schema 评审（30 min） | yx + zwx |
| Week 2 末 | chunks 格式确认 + 首批数据交付 | yx + zwx + zf |
| Week 3 初 | KnowledgeItem content schema 评审（1h） | yx + zf + 导师 |
| Week 4 末 | 训练样本质量评审 | yx + zf |
| Week 5 末 | RetrievalResult 集成测试 | yx + zwx |
| Week 6 末 | Opportunity candidate 评分会议 | yx + zf + 导师 |
| Week 7 末 | 微调模型集成测试 | yx + zf |

---

## 数据存储位置约定

```
GapMind/
├── data/                       # 新建目录，存放所有交付数据
│   ├── chunks/                 # Contract #1
│   │   └── {workspace_id}/
│   │       └── {paper_id}.jsonl
│   ├── knowledge_exports/      # Contract #2 导出
│   │   └── {workspace_id}_{date}.jsonl
│   ├── training/               # Contract #3, #5
│   │   ├── extract_train.jsonl
│   │   ├── extract_val.jsonl
│   │   ├── extract_test.jsonl
│   │   ├── opportunity_train.jsonl
│   │   └── training_samples_split.json
│   └── evaluations/            # 评估结果
│       └── {experiment_name}_{date}.json
├── models/                     # zf 产出的 LoRA adapter（git LFS 或外部存储）
│   ├── gapmind-qwen-extract-v1/
│   └── gapmind-qwen-opportunity-v1/
└── training/                   # zf 的训练脚本
    ├── train_extract.py
    ├── train_opportunity.py
    └── evaluate.py
```

**`.gitignore` 建议**：`data/` 和 `models/` 不进 git（太大），用云盘 / NAS 共享。`training/` 进 git。

---

## 验证脚本

每个契约都应有对应的验证脚本，放在 `scripts/validate/`：

- `validate_chunks.py`：校验 Contract #1 格式
- `validate_knowledge_items.py`：校验 Contract #2 格式
- `validate_training_samples.py`：校验 Contract #3 格式
- `validate_retrieval_result.py`：校验 Contract #4 格式

队友交付数据前必须跑验证脚本。**不合规的数据不接受**。

---

## 常见问题

### Q: chunk 太长（超过 800 tokens）怎么办？

A: 强制切分。如果切分会破坏句子，在句子边界切；句子还是太长，在词边界切。`chunk_index` 递增，`text` 字段记录切分后的内容。

### Q: 同一个 method 在不同论文里被抽成不同的 canonical_name 怎么办？

A: 这是已知问题。Phase 3 的解决方案：
1. 抽取后做 canonical_name 归一化（小写 + 去标点 + 去空格）
2. 高频名字建立 alias 表（如 `GNNExplainer` / `GNN-Explainer` / `gnn explainer` → `GNNExplainer`）
3. Phase 5 可以用 LLM 做 entity resolution

### Q: zf 的微调模型输出不合规 JSON 怎么办？

A: 在yx的 service 层加 schema validator：
```python
def parse_extraction_output(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 重试一次，prompt 加 "Return only valid JSON"
        ...
    validate_schema(data)  # 用 Pydantic
    return data
```

### Q: 如何处理跨 workspace 的数据？

A: **不允许**。所有数据都按 workspace_id 隔离。zwx 的检索、zf 的训练数据都严格按 workspace 分。未来需要跨 workspace 分析（如 benchmark）时，导出为单独数据集处理。

### Q: zf 的训练数据可以从哪些论文来？

A: 三种来源：
1. **Smoke Set**（8-12 篇 GNN 论文）：yx + zf 一起标注 gold（Phase 2-3 早期用）
2. **Development Set**（扩展到 30-40 篇）：正式 LoRA 训练用，由 Smoke Set + 新增论文组成
3. **公开数据集**（如 SciERC、SciREX）：已有的学术抽取数据集，可作为预训练/对比
2. **arXiv 爬取**（建议 Phase 3 后）：用 Semantic Scholar API 拉更多论文
3. **公开数据集**（如 SciERC、SciREX）：已有的学术抽取数据集，可作为预训练

### Q: 如果导师要求某个字段调整怎么办？

A: 走变更流程：修改本契约 → 版本号 +1 → 通知所有相关方 → 已有数据按需迁移。**不要私下改字段名**。

---

## 附录：与队友沟通的 Check List

### 给zwx 的 Check List

- [ ] 读了 [plans.md](plans.md)、[db_schema.md](db_schema.md)、[api_reference.md](api_reference.md)、本契约
- [ ] 理解 Contract #1（chunks）和 Contract #4（RetrievalResult）
- [ ] 确认 Milvus schema 与yx一致
- [ ] 知道 chunk 文件路径约定 `data/chunks/{workspace_id}/{paper_id}.jsonl`
- [ ] 知道检索函数签名（`semantic_search` / `find_similar_work` / `find_counter_evidence`）
- [ ] 知道性能契约（< 200ms、不抛异常、workspace 隔离）

### 给zf 的 Check List

- [ ] 读了 [plans.md](plans.md)、[db_schema.md](db_schema.md)、本契约
- [ ] 理解 Contract #2（KnowledgeItem content schema，5 种类型）
- [ ] 理解 Contract #3（训练样本格式）
- [ ] 理解 Contract #5（opportunity candidates，Phase 5 用）
- [ ] 理解 Contract #6（LLM provider 部署要求）
- [ ] 确认基座模型选择（已定：**Qwen3:8B**）
  - 抽取任务：用 non-thinking 模式（输出更稳定）
  - Opportunity 生成：可用 thinking 模式（需要推理）
  - 硬件（任一即可）：
    - 单张 A100 40G/80G（标准 LoRA BF16，最舒服）
    - 单张 3090 24GB（**够用**，需用 QLoRA 4bit 量化 + gradient accumulation + activation checkpointing）
    - 2 张 3090（DDP，batch 翻倍，训练更快但非必需）
  - 框架：LLaMA-Factory 或 Axolotl（确认 Qwen3 支持）
  - 部署：vLLM 推理比训练省显存，3090 单卡可部署 BF16，3060 12GB 可部署 AWQ 4bit
- [ ] 知道训练数据划分原则（按 paper_id，不按 chunk）
- [ ] 知道数据交付路径 `data/training/`
- [ ] 知道 schema_version 字段必须填写

### 给自己的 Check List

- [ ] 完成 Phase 2 PDF 解析（产出 chunks）
- [ ] 在 `backend/app/domains/retrieval/service.py` 留好zwx 的接口位置
- [ ] 在 `backend/app/gateway/llm.py` 设计多 provider 架构
- [ ] 创建 `data/` 目录并加入 `.gitignore`
- [ ] 写验证脚本 `scripts/validate/`
- [ ] 与导师确认 5 种 KnowledgeItem content schema 是否合理
- [ ] 与队友约定每周 sync 时间
