# GapMind 队友 Onboarding 指南

> 这份文档帮助你（队友）快速了解 GapMind 项目、你的角色、以及四人如何协作。
> 读完这份文档后，你应该能回答：**项目是做什么的 / 我负责什么 / 我的输入从哪来 / 我的输出给谁 / 什么时候交付**。
> 最后更新：2026-07-21

---

## 一、30 秒了解 GapMind

GapMind 是一个面向 AI/CS 研究者（重点是硕博生）的 **Human-in-the-Loop AI Research Workspace**。核心价值是 **Evidence-grounded Research Opportunity Discovery**——基于多篇论文证据链发现研究机会，且所有 AI 产出都需用户确认。

**不是什么**：不是通用 RAG 系统、不是 LLM 论文总结工具、不是 Workflow 强制工具。

**核心闭环**：
```
Research Topic / Papers
→ PDF 解析 + 结构化抽取
→ Knowledge Graph + 证据检索
→ Similar Work / Counter-Evidence 检索
→ Research Opportunity Proposal (Agent 只能提出 Candidate)
→ 用户确认（Accept / Edit / Reject / Defer）
→ Research Question / Hypothesis / Validation Plan
→ Timeline 自动记录 + Workspace Memory
```

**旗舰差异化**：
- 证据链可追溯（每个结论都能回到论文原文）
- 反证发现（不仅找支持证据，还必须找反驳证据）
- Workspace 而非 Workflow（用户从任意模块独立进入，不强制步骤）

---

## 二、四人分工与角色

| 角色 | 负责人 | 主要职责 |
|------|--------|---------|
| **项目核心 / 基础设施** | yx | Workspace/Paper/Task/Timeline/Knowledge 数据层与 API、PDF 解析 pipeline、Discover Agent、Plan 生成、Publish/Respond 流程、前端、整体集成 |
| **RAG 子系统** | zwx | Milvus 向量检索、3 个检索函数（semantic search / similar work / counter-evidence）、可选 GraphRAG |
| **微调（LoRA）** | zf | Qwen3:8B 微调：结构化抽取模型 + Opportunity 生成模型；构造训练数据；vLLM 部署 |
| **Execute + Analyze** | ygl | Baseline Reproduction Checklist 跟踪、实验记录管理（Execute）；实验结果分析、异常发现、验证建议（Analyze） |

### 关键原则

1. **所有代码进同一个 Git 仓库**，不另起 repo
2. **所有队友的工作是 GapMind 的"能力插件"**，不是独立项目
3. **数据交付走 [data_contracts.md](data_contracts.md)**——这是四人协作的正式契约，任何字段变更必须先改契约再通知
4. **每周一次 sync**，确保接口对齐

---

## 三、完整功能流程图

> 这张图展示**每个人每一步的输入和输出，以及四人之间的衔接点**，覆盖 MVP 全周期（P0→P3）。
> 衔接点 = Contract（见 [data_contracts.md](data_contracts.md)）
> 优先级标注：**P0** = 必须做深（MVP 旗舰），**P1** = 轻量实现，**P2/P3** = 后续再做

### 3.0 功能优先级全景

| Step | 功能 | 优先级 | 谁负责 | 状态 |
|------|------|--------|--------|------|
| ① | Workspace & Paper 管理 | P0 | yx | ✅ 已完成 |
| ② | PDF 解析 Pipeline | P0 | yx | ✅ 已完成 |
| ③ | Research Topic Exploration（Semantic Scholar 搜索） | P0 | yx + zwx | 🚧 Phase 2-3 |
| ④ | Milvus Embedding + 索引 | P0 | zwx | 🚧 Phase 3 |
| ⑤ | 检索函数（semantic / similar / counter-evidence） | P0 | zwx | 🚧 Phase 3 |
| ⑥ | Knowledge 抽取 | P0 | yx + zf | 🚧 Phase 3 |
| ⑦ | 训练数据 + LoRA 微调 + vLLM 部署 | P0/P1 | zf | 🚧 Phase 3-5 |
| ⑧ | Discover Agent（Research Opportunity Discovery） | P0 | yx | 🚧 Phase 5 |
| ⑨ | HITL 确认 + 转 Research Plan | P0/P1 | yx + 用户 | 🚧 Phase 4-5 |
| ⑩ | Plan：Research Question / Hypothesis / Validation Plan / Baseline Checklist | P1 | yx + zf | 🚧 Phase 5-6 |
| ⑪ | Execute：Reproduction Checklist + 实验记录 | P1 | **ygl** + yx | 🚧 Phase 7 |
| ⑫ | Analyze：实验结果分析 + 异常发现 + 验证建议 | P2 | **ygl** + zf（可选） | 🚧 后续 |
| ⑬ | Publish：大纲 + 段落 + 引用建议 | P2 | yx + zf | 🚧 后续 |
| ⑭ | Respond：Reviewer Comments 回复策略 + 修订计划 | P3 | yx + zf | 🚧 后续 |

### 3.1 总体数据流（P0 核心闭环）

下图展示 **P0 阶段的核心闭环**——从用户上传 PDF 到 Discover Agent 产出 Opportunity。P1/P2/P3 是这个闭环的下游延伸，在 3.2 节按 Step 详述。

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        用户（researcher）                              │
│   创建 Workspace → 上传 PDF → 查看 Knowledge → 确认 Opportunity        │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          yx（项目核心）                                 │
│                                                                         │
│  ① Workspace/Paper/Task 管理                                            │
│     输入: 用户操作                                                      │
│     输出: papers 表记录 + PDF artifact                                  │
│              │                                                          │
│              ▼                                                          │
│  ② PDF 解析 Pipeline（Phase 2 已完成）                                  │
│     输入: PDF artifact                                                  │
│     输出: parsed_text artifact + chunks JSONL                           │
│              │                                                          │
│              │ Contract #1: chunks                                      │
│              ▼                                                          │
└──────────────┼──────────────────────────────────────────────────────────┘
               │
       ┌───────┴───────┐
       ▼               ▼
┌──────────────┐  ┌──────────────────────────────────────────────────────┐
│  zwx (RAG)   │  │  yx 继续处理                                          │
│              │  │                                                       │
│  ③ Milvus    │  │  ⑥ Knowledge 抽取（Phase 3，用 Deepseek 基线）       │
│     Embedding│  │     输入: chunks                                      │
│     + 索引   │  │     输出: KnowledgeItem + EvidenceSpan                │
│              │  │              │                                        │
│  ⑤ 检索函数  │  │              │ Contract #2: knowledge_items           │
│     - semanti│  │              ├────────────────────────┐               │
│     - similar│  │              │                        ▼               │
│     - counter│  │              │           ┌──────────────────────┐     │
│              │  │              │           │  zf（微调）           │     │
│  输出:       │  │              │           │                       │     │
│  Retrieval   │  │              │  Contract │  ⑦a 构造训练数据      │     │
│  Result      │  │              │  #2 作为  │     输入: parsed_text   │     │
│              │  │              │  训练目标 │     + KnowledgeItems  │     │
│  Contract #4 │  │              │           │     输出: 训练样本     │     │
│              │  │              │           │              │        │     │
│              │  │              │           │              │Contract#3   │
│              │  │              │           │              ▼        │     │
│              │  │              │           │  ⑦b LoRA 微调          │     │
│              │  │              │           │     Qwen3:8B           │     │
│              │  │              │           │     - extract adapter  │     │
│              │  │              │           │     - opportunity      │     │
│              │  │              │           │              │        │     │
│              │  │              │           │              ▼        │     │
│              │  │              │           │  ⑦c vLLM 部署          │     │
│              │  │              │           │     OpenAI 兼容 API    │     │
│              │  │              │           │              │        │     │
│              │  │              │           │              │Contract#6   │
│              │  │              │           │              ▼        │     │
└──────┬───────┘  │              │           └──────────────┼────────┘     │
       │          │              │                          │              │
       │Contract#4               │                          │              │
       ▼          │              │                          │              │
┌──────────────────────────────────────────────────────────┐│              │
│  yx: Discover Agent（Phase 5）                            │▼              │
│                                                           │               │
│  ⑧ Discover Agent run                                     │               │
│     输入: KnowledgeItems + RetrievalResults               │               │
│     调用: LLM Gateway（Deepseek 或 zf 微调模型）          │               │
│     输出: Opportunity Candidates                          │               │
│              │                                            │               │
│              │ Contract #5: opportunity_candidates        │               │
│              └──────────────────────────────┐             │               │
│                                             ▼             │               │
│                          ┌────────────────────────────────────────┐       │
│                          │  zf: Opportunity 微调（Phase 5+）      │       │
│                          │     输入: candidates + 人工评分        │       │
│                          │     输出: 微调后的 opportunity 模型    │       │
│                          └────────────────────────────────────────┘       │
│                                                                           │
│  ⑨ 用户确认（HITL，Phase 4）                                             │
│     输入: Opportunity Candidate                                           │
│     输出: Confirmed Opportunity → Research Plan                           │
└───────────────────────────────────────────────────────────────────────────┘
                               │
                               ▼ P1/P2/P3 下游延伸（见 3.2 详述）
                    ⑩ Plan → ⑪ Execute → ⑫ Analyze → ⑬ Publish → ⑭ Respond
```

### 3.2 按 Step 的详细输入/输出（P0→P3 全周期）

下面把每个 Step 拆细，明确**谁做、输入是什么、输出是什么、给谁**。

#### Step ①：Workspace & Paper 管理（yx）【P0】

| 维度 | 内容 |
|------|------|
| **执行者** | yx（已完成 Phase 1a/1b） |
| **触发** | 用户在前端创建 Workspace、上传 PDF |
| **输入** | 用户操作 + PDF 文件 |
| **处理** | 创建 Workspace 行 / Paper 行 + PDF artifact + 自动提取 PDF metadata（title/authors/year） |
| **输出** | `papers` 表记录 + `artifacts` 表记录（PDF 存本地 `backend/storage/`） |
| **下游消费者** | Step ②（yx 自己的 parse_pdf task） |
| **Contract** | 无（内部数据） |
| **状态** | ✅ 已完成 |

#### Step ②：PDF 解析 Pipeline（yx）【P0】

| 维度 | 内容 |
|------|------|
| **执行者** | yx（已完成 Phase 2） |
| **触发** | Paper upload 或 attach-pdf 后自动 spawn Celery task |
| **输入** | PDF artifact（`papers.primary_artifact_id` 指向） |
| **处理** | PyMuPDF 提取全文 → 启发式章节识别 → 三级分块（512±50 token，重叠 50） |
| **输出** | <ul><li>`parsed_text` artifact（.txt 文件）</li><li>`chunk_index` artifact（.jsonl 文件）</li><li>`data/chunks/{workspace_id}/{paper_id}.jsonl`（**Contract #1 交付文件**）</li><li>`papers.parse_status = "parsed"`, `chunk_count = N`</li><li>Timeline 事件 `paper.parsed`</li></ul> |
| **下游消费者** | zwx（建 Milvus 索引） + zf（构造训练数据） |
| **Contract** | **#1: chunks** |
| **状态** | ✅ 已完成 |

#### Step ③：Research Topic Exploration（yx + zwx）【P0】

| 维度 | 内容 |
|------|------|
| **执行者** | yx（API + 前端）+ zwx（可选：检索增强搜索） |
| **触发** | 用户在 Discover 入口输入研究主题和关键词 |
| **输入** | Topic / Keywords / Goals / Constraints（Workspace Research Profile） |
| **处理** | <ul><li>调用 Semantic Scholar API 搜索论文</li><li>返回候选论文列表（含元数据）</li><li>用户选择导入 → 自动调 Step ① upload 流程</li></ul> |
| **输出** | <ul><li>候选论文列表（前端展示）</li><li>用户选定后创建 Paper 行 + PDF artifact（回到 Step ① 流程）</li></ul> |
| **下游消费者** | 用户（选论文）→ Step ① → Step ② |
| **Contract** | 无（Semantic Scholar API 是外部接口） |
| **状态** | 🚧 待实现（Phase 2-3） |
| **备注** | plans.md 的 P0 功能，目前只支持手动上传 PDF，S2 搜索是下一步要加的 |

#### Step ④：Milvus Embedding + 索引（zwx）【P0】

| 维度 | 内容 |
|------|------|
| **执行者** | zwx |
| **触发** | yx 解析完一篇 paper 后调用 `index_chunks(paper_id)`（或 zwx 自己 poll） |
| **输入** | `data/chunks/{ws}/{paper}.jsonl`（Contract #1） |
| **处理** | <ul><li>读 chunks JSONL</li><li>调用 BGE-m3 API 向量化（通过 yx 提供的 `EmbeddingGateway`）</li><li>写入 Milvus `paper_chunks` collection</li></ul> |
| **输出** | Milvus 索引（可查询状态） |
| **下游消费者** | Step ⑤（zwx 自己的检索函数） |
| **Contract** | 无（内部状态） |
| **状态** | 🚧 待实现（Phase 3） |

#### Step ⑤：检索函数（zwx）【P0】

| 维度 | 内容 |
|------|------|
| **执行者** | zwx |
| **触发** | yx 的 Discover Agent 调用 / 前端检索 UI 调用 |
| **输入** | <ul><li>`semantic_search(workspace_id, query, top_k)`</li><li>`find_similar_work(workspace_id, paper_id, top_k)`</li><li>`find_counter_evidence(workspace_id, claim_text, top_k)`</li></ul> |
| **处理** | Milvus 向量检索 + 可选 reranker + 可选 GraphRAG 增强（利用 knowledge_relations） |
| **输出** | `RetrievalResponse`（含 `list[RetrievalResult]`） |
| **下游消费者** | yx 的 Discover Agent（Step ⑧） + 前端检索 UI + Step ⑫ Analyze（找相关证据） |
| **Contract** | **#4: RetrievalResult** |
| **状态** | 🚧 待实现（Phase 3） |

#### Step ⑥：Knowledge 抽取（yx + zf）【P0】

| 维度 | 内容 |
|------|------|
| **执行者** | yx（基线，用 Deepseek）→ zf（微调模型替代） |
| **触发** | Paper 解析完成后自动 spawn `extract_knowledge` task |
| **输入** | <ul><li>**`parsed_text`（论文全文或章节级 text）**——不是 chunks</li><li>论文元数据（title / authors / year / abstract）作为 prompt 上下文</li><li>5 种类型的 schema 定义（Contract #2）作为 system prompt</li></ul> |
| **处理** | <ul><li>对**整篇论文**（或按章节切分，每章一次调用）调 LLM（Deepseek 或 zf 微调模型）</li><li>LLM 输出结构化 JSON：<ul><li>5 种类型的 KnowledgeItem（method/task/dataset/claim/limitation）</li><li>KnowledgeRelation（Paper→Method、Method→Task 等）</li><li>每个 item 的 source_provenance（指向 `parsed_text` 的 char offset）</li><li>evidence_text（原文引用片段）</li></ul></li><li>schema 验证 + 写入 `knowledge_items` 表</li><li>建立 `knowledge_relations` 表</li><li>建立 `evidence_spans` 表（指向 `parsed_text_artifact_id` + char offset）</li></ul> |
| **输出** | <ul><li>`knowledge_items` 表记录（含 source_provenance 指向 parsed_text）</li><li>`knowledge_relations` 表记录</li><li>`evidence_spans` 表记录（指向 parsed_text，不是 chunk）</li></ul> |
| **下游消费者** | <ul><li>yx 的 Discover Agent（Step ⑧）</li><li>zf（作为微调训练的 ground truth 目标，Step ⑦a）</li><li>前端 Knowledge 列表页</li><li>Step ⑩ Plan（基于已确认的知识生成研究计划）</li><li>Step ⑬ Publish（引用证据）</li></ul> |
| **Contract** | **#2: knowledge_items** |
| **状态** | 🚧 待实现（Phase 3） |
| **关键设计澄清**（zf 反馈驱动） | <ul><li>**输入是 `parsed_text`**（论文全文或章节级），**不是 chunks**——大部分 chunk 没有有效的结构化知识，chunk 级抽取会浪费且切断上下文</li><li>LLM 需要看**整篇论文**才能正确判断实体关系（如同一方法在不同章节出现应归为同一 canonical_name）</li><li>**超长论文（>8K token，超出 LLM context window）**：按章节切分（Abstract/Intro/Method/Experiment/Conclusion），每章一次 LLM 调用，结果合并</li><li>**evidence 指向 `parsed_text`**：用 char offset 定位回全文，不依赖 chunks</li><li>chunks 是 Contract #1 的产物（Contract #4），不是抽取的输入——chunks 只用于向量检索（zwx 的 RAG），不进 LLM</li></ul> |

#### Step ⑦：训练数据 + LoRA 微调 + vLLM 部署（zf）【P0/P1】

这个 Step 有三个子阶段：⑦a 构造训练数据 → ⑦b LoRA 微调 → ⑦c vLLM 部署。

| 维度 | 内容 |
|------|------|
| **执行者** | zf（与 yx 协作） |
| **触发** | yx 产出 KnowledgeItems 后（⑦a）；训练数据就绪后（⑦b）；微调完成（⑦c） |
| **输入（⑦a）** | <ul><li>**`parsed_text`**（论文全文，yx 的 parse_pdf 任务产出，Phase 2 已完成）</li><li>KnowledgeItems（Contract #2，yx 的 extract_knowledge 任务产出，作为 ground truth 目标）</li></ul> |
| **处理（⑦a）** | <ul><li>Silver 阶段：yx 用 Deepseek 跑 30-40 篇论文的 parsed_text，产出 ~1500-2000 个 silver 样本</li><li>Gold 阶段：zf + yx 从 silver 抽 300-500 个修正为 gold</li><li>训练样本 input 是 parsed_text（整篇或章节级），**不是 chunks**</li><li>转换为 ShareGPT/Alpaca 格式（Contract #3）</li><li>按 paper_id 划分 train/val/test（80/10/10）</li></ul> |
| **输入（⑦b）** | 训练样本（Contract #3） |
| **处理（⑦b）** | <ul><li>基座：Qwen3:8B</li><li>方法：QLoRA（单张 3090 够）或标准 LoRA（A100）</li><li>框架：LLaMA-Factory 或 Axolotl</li><li>抽取任务用 non-thinking 模式，Opportunity 生成可用 thinking 模式</li><li>训练两个 adapter：`gapmind-extract-v1` + `gapmind-opportunity-v1`</li></ul> |
| **处理（⑦c）** | 用 vLLM 部署微调模型，暴露 OpenAI 兼容 HTTP API |
| **输出** | <ul><li>`data/training/*.jsonl`（训练样本）</li><li>LoRA adapter weights（存 `models/` 目录，git LFS 或外部存储）</li><li>评估报告（vs Deepseek baseline）</li><li>vLLM HTTP 服务（OpenAI 兼容）</li></ul> |
| **下游消费者** | yx 的 Model Gateway（Step ⑧/⑩/⑫/⑬/⑭ 调用） |
| **Contract** | **#3: training_samples** + **#6: LLM provider 接口** |
| **状态** | 🚧 待实现（Phase 3-5） |

#### Step ⑧：Discover Agent（yx）【P0 旗舰】

| 维度 | 内容 |
|------|------|
| **执行者** | yx |
| **触发** | 用户在前端点 "Run Discover" |
| **输入** | <ul><li>KnowledgeItems（Contract #2）</li><li>RetrievalResults（Contract #4，来自 zwx）</li><li>LLM provider（Deepseek 或 zf 微调模型，通过 Model Gateway）</li></ul> |
| **处理** | <ul><li>Literature Search → Knowledge Graph Query</li><li>Similar Work Retrieval（调 zwx 的 `find_similar_work`）</li><li>Counter-Evidence Retrieval（调 zwx 的 `find_counter_evidence`）</li><li>Opportunity Synthesis（LLM 综合）</li></ul> |
| **输出** | Opportunity Candidates（`knowledge_items` 表，type=`opportunity`，status=`candidate`） |
| **下游消费者** | <ul><li>用户（前端确认 UI）</li><li>zf（作为 Opportunity 微调训练数据，Step ⑦b 第二阶段）</li></ul> |
| **Contract** | **#5: opportunity_candidates** |
| **状态** | 🚧 待实现（Phase 5） |

#### Step ⑨：HITL 确认 + 转 Research Plan（yx + 用户）【P0/P1】

| 维度 | 内容 |
|------|------|
| **执行者** | 用户（前端操作） + yx（后端流程） |
| **触发** | 用户看到 Opportunity Candidate 后点 Accept/Edit/Reject/Defer |
| **输入** | Opportunity Candidate |
| **处理** | <ul><li>用户编辑后确认 → status=`confirmed`</li><li>Timeline 记录 `opportunity.confirmed`</li><li>转化为 Research Question / Hypothesis / Validation Plan</li></ul> |
| **输出** | Confirmed Opportunity + 初始 Research Plan（`knowledge_items` 表，type=`research_plan`，status=`candidate`） |
| **下游消费者** | Step ⑩ Plan（细化研究计划） |
| **Contract** | 无（内部数据） |
| **状态** | 🚧 待实现（Phase 4-5） |

#### Step ⑩：Plan（yx + zf）【P1】

| 维度 | 内容 |
|------|------|
| **执行者** | yx（API + 流程）+ zf（Plan 生成微调模型，可选） |
| **触发** | 用户在 Confirmed Opportunity 上点 "Generate Plan" |
| **输入** | <ul><li>Confirmed Opportunity（Step ⑨）</li><li>相关 KnowledgeItems（method/task/dataset，作为 plan 依据）</li><li>可选：GitHub repo 链接（用户填）</li></ul> |
| **处理** | LLM 生成： <ul><li>Research Question（明确的研究问题）</li><li>Hypothesis（可验证的假设）</li><li>Validation Plan（验证方案：用什么数据集、跑什么实验、对比什么 baseline）</li><li>Baseline Reproduction Checklist（基于 Paper / GitHub repo 生成的复现步骤清单）</li></ul> |
| **输出** | <ul><li>`knowledge_items` 行：type=`research_question` / `hypothesis` / `research_plan`</li><li>`baseline_checklist`（可能新建表或用 artifact 存 JSON）</li><li>Timeline 事件 `plan.generated`</li></ul> |
| **下游消费者** | <ul><li>用户（查看/编辑 plan）</li><li>Step ⑪ Execute（基于 plan 跑实验）</li></ul> |
| **Contract** | 待定（可能新增 Contract #7: research_plan，见 [data_contracts.md](data_contracts.md) 后续版本） |
| **状态** | 🚧 待实现（Phase 5-6） |

#### Step ⑪：Execute（ygl + yx）【P1】

| 维度 | 内容 |
|------|------|
| **执行者** | **ygl（主）** + yx（API/数据层支持） + 用户（实际跑实验） |
| **触发** | 用户基于 Baseline Checklist 开始跑实验 |
| **输入** | <ul><li>Baseline Checklist（Step ⑩）</li><li>用户上传的实验日志 / 指标 / 图表</li></ul> |
| **处理** | <ul><li>**不自动跑 GitHub repo**（plans.md 明确不做）</li><li>ygl 提供 Checklist 跟踪界面，用户手动勾选步骤</li><li>用户上传实验产物（日志、指标 JSON、图表 PNG）作为 artifact</li><li>记录实验状态：not_started / in_progress / completed / failed</li><li>ygl 设计实验记录的数据模型（可能新建 `experiments` 表，或用 `knowledge_items` type=`experiment`）</li></ul> |
| **输出** | <ul><li>实验记录（`experiments` 表或 `knowledge_items` type=`experiment`）</li><li>实验 artifact（logs / metrics / plots）</li><li>Timeline 事件 `experiment.completed`</li></ul> |
| **下游消费者** | <ul><li>Step ⑫ Analyze（ygl 自己分析实验结果）</li><li>用户（跟踪实验进度）</li></ul> |
| **Contract** | 待定（Contract #8: experiment_record，ygl 起草） |
| **状态** | 🚧 待实现（Phase 7） |
| **重要约束** | **不自动运行任意 GitHub 仓库**（plans.md 明确不做） |

#### Step ⑫：Analyze（ygl + zf）【P2】

| 维度 | 内容 |
|------|------|
| **执行者** | **ygl（主）** + zf（Analyze 微调模型，可选） |
| **触发** | 用户上传完实验结果后点 "Analyze" |
| **输入** | <ul><li>实验日志 / 指标 / 图表（Step ⑪）</li><li>Hypothesis（Step ⑩，验证目标）</li><li>相关 KnowledgeItems + RetrievalResults（对比已有工作的结果）</li></ul> |
| **处理** | LLM 分析（ygl 实现）： <ul><li>解释结果（是否符合 hypothesis）</li><li>发现异常（unexpected metrics / outliers）</li><li>建议验证（需要补哪些实验）</li><li>对比已有工作（调 zwx 检索类似实验结果）</li></ul> |
| **输出** | <ul><li>分析报告（artifact）</li><li>`knowledge_items` 行：type=`finding`（实验发现）</li><li>可能更新 Hypothesis 状态：confirmed / rejected / partial</li></ul> |
| **下游消费者** | <ul><li>用户（决策下一步）</li><li>Step ⑬ Publish（基于 finding 写论文）</li></ul> |
| **Contract** | 待定（Contract #9: analysis_report，ygl 起草） |
| **状态** | 🚧 待实现（后续 Phase） |

#### Step ⑬：Publish（yx + zf）【P2】

| 维度 | 内容 |
|------|------|
| **执行者** | yx（API + 流程）+ zf（Publish 微调模型，可选） |
| **触发** | 用户有 Confirmed Finding 后点 "Start Writing" |
| **输入** | <ul><li>Confirmed Opportunity + Research Plan（Step ⑨/⑩）</li><li>Confirmed Findings（Step ⑫）</li><li>相关 KnowledgeItems + EvidenceSpans（引用素材）</li><li>用户已有的 Draft（可选）</li></ul> |
| **处理** | LLM 辅助写作： <ul><li>大纲生成（基于 Research Plan）</li><li>段落建议（基于 EvidenceSpans，每段都带引用）</li><li>引用建议（从 KnowledgeItems 中找合适引用）</li><li>**不自动生成完整论文**，是"辅助"而非"代写"</li></ul> |
| **输出** | <ul><li>大纲（artifact，JSON 或 Markdown）</li><li>段落建议列表（每段附 evidence_span_ids）</li><li>引用列表（Citation 类型的 KnowledgeItems）</li><li>Draft artifact（用户编辑后的草稿）</li></ul> |
| **下游消费者** | <ul><li>用户（编辑论文）</li><li>Step ⑭ Respond（提交后处理审稿）</li></ul> |
| **Contract** | 待定 |
| **状态** | 🚧 待实现（后续 Phase） |
| **重要约束** | **任何对外输出前必须用户确认**（HITL 核心原则） |

#### Step ⑭：Respond（yx + zf）【P3】

| 维度 | 内容 |
|------|------|
| **执行者** | yx（API + 流程）+ zf（Respond 微调模型，可选） |
| **触发** | 用户收到审稿意见后上传 Reviewer Comments |
| **输入** | <ul><li>Reviewer Comments（用户上传）</li><li>Draft（Step ⑬）</li><li>相关 KnowledgeItems + EvidenceSpans（回应证据）</li></ul> |
| **处理** | LLM 分析： <ul><li>解析 Reviewer Comments（分类：主要问题 / 次要问题 / 建议）</li><li>生成回复策略（point-by-point response）</li><li>生成修订计划（哪些段落要改、怎么改）</li><li>**不自动回复**，是"建议"而非"代答"</li></ul> |
| **输出** | <ul><li>回复策略文档（artifact）</li><li>修订计划（artifact）</li><li>每条 reviewer comment 对应的 suggested response（带 evidence）</li></ul> |
| **下游消费者** | 用户（编辑回复 + 修订论文） |
| **Contract** | 待定 |
| **状态** | 🚧 待实现（后续 Phase，MVP 可能不做） |

### 3.3 Contract 衔接速查表（已定义）

| Contract | 生产者 | 消费者 | 何时交付 | 状态 |
|----------|--------|--------|---------|------|
| #1 chunks | yx (Step ②) | **zwx (Step ④) only** | Phase 2 完成后持续 | ✅ |
| #2 knowledge_items | yx (Step ⑥) + zf (Step ⑦b) | yx Discover Agent (Step ⑧) + Step ⑩/⑬ | Phase 3 持续 | 🚧 |
| #3 training_samples | zf (Step ⑦a) | zf 自己 (Step ⑦b) | Phase 3 中后期 | 🚧 |
| #4 RetrievalResult | zwx (Step ⑤) | yx Discover Agent (Step ⑧) + Step ⑫ Analyze | Phase 3 | 🚧 |
| #5 opportunity_candidates | yx (Step ⑧) | zf (Opportunity 微调) + 用户 + Step ⑨ | Phase 5 | 🚧 |
| #6 LLM provider | zf (Step ⑦c) | yx Model Gateway（Step ⑧/⑩/⑫/⑬/⑭ 调用） | Phase 3-5 | 🚧 |

### 3.4 待定义 Contract（P1/P2/P3 阶段会补）

P1/P2/P3 的 Step 涉及新数据对象，后续会在 [data_contracts.md](data_contracts.md) 里补 Contract：

| 待定义 Contract | 涉及 Step | 谁起草 | 内容 |
|-----------------|-----------|--------|------|
| #7 research_plan | ⑩ ⑪ | yx | Research Plan + Baseline Checklist 的格式 |
| #8 experiment_record | ⑪ ⑫ | **ygl** | 实验记录（日志/指标/图表）的格式 |
| #9 analysis_report | ⑫ | **ygl** | 分析报告 + Finding 的格式 |
| #10 writing_asset | ⑬ ⑭ | yx | 大纲 / 段落建议 / 引用列表 / Draft 的格式 |
| #11 reviewer_response | ⑭ | yx | 回复策略 + 修订计划的格式 |

这些 Contract 会在对应 Phase 开始前由负责人起草，与队友 + 导师评审后定稿。

### 3.5 四人在各 Step 的参与度矩阵

帮助队友快速看清楚：**每个 Step 我参不参与、是什么角色**。

| Step | 功能 | yx | zwx | zf | ygl |
|------|------|-----|-----|-----|------|
| ① Workspace & Paper 管理 | **主** | — | — | — |
| ② PDF 解析 | **主** | — | — | — |
| ③ Research Topic Exploration | **主**（API） | 辅（可选：检索增强搜索） | — | — |
| ④ Milvus Embedding + 索引 | — | **主** | — | — |
| ⑤ 检索函数 | 调用方 | **主** | — | — |
| ⑥ Knowledge 抽取 | **主**（Deepseek 基线） | — | 替代（微调模型） | — |
| ⑦a 构造训练数据 | 协作（提供 silver） | — | **主** | — |
| ⑦b LoRA 微调 | — | — | **主** | — |
| ⑦c vLLM 部署 | — | — | **主** | — |
| ⑧ Discover Agent | **主** | 调用方（检索） | 替代（Opportunity 微调模型） | — |
| ⑨ HITL 确认 | **主**（后端） | — | — | — |
| ⑩ Plan | **主**（流程） | — | 可选（Plan 微调） | — |
| ⑪ Execute | 协作（API/数据层） | — | — | **主** |
| ⑫ Analyze | — | 调用方（对比检索） | 可选（Analyze 微调） | **主** |
| ⑬ Publish | **主**（流程） | — | 可选（Publish 微调） | — |
| ⑭ Respond | **主**（流程） | — | 可选（Respond 微调） | — |

**关键观察**：
- **yx 是所有 Step 的"骨架"**——即使 zf 的微调模型没就绪，yx 用 Deepseek 也能跑通整个流程
- **zwx 的核心工作集中在 Step ④⑤**——RAG 子系统是 Discover Agent 和 Analyze 的依赖
- **zf 的工作是"优化"而非"必需"**——Phase 3-5 先用 Deepseek 跑通，zf 的微调模型就绪后接入 Model Gateway 替换，提升质量降低成本
- **ygl 的核心工作集中在 Step ⑪⑫**——Execute + Analyze 是 P1/P2 阶段的功能，前期可以不投入，等 Phase 7 再启动
- **P2/P3 阶段 zf 的参与是"可选"**——如果时间紧，Publish/Respond 可以只用 Deepseek，不微调

---

## 四、环境搭建（队友第一次跑）

### 4.1 前置要求

- Docker Desktop（用于 PostgreSQL / Redis / Milvus）
- Python 3.11+
- Node.js 18+
- GPU（仅 zf 需要，单张 3090 24GB 即可，建议用 QLoRA）

### 4.2 克隆并启动

```bash
# 1. 克隆仓库
git clone <repo-url>
cd GapMind

# 2. 复制环境变量模板，填入自己的 API key
cp infra/.env.example backend/.env
# 编辑 backend/.env，填入：
#   DEEPSEEK_API_KEY=sk-...
#   SILICONFLOW_API_KEY=sk-...
#   SEMANTIC_SCHOLAR_API_KEY=s2-...（可选）

# 3. 启动基础设施（PostgreSQL + Redis + Milvus）
cd infra
docker compose up -d
docker compose ps   # 确认 5 个服务 healthy

# 4. 后端
cd ../backend
python -m venv .venv
.venv\Scripts\activate           # Windows
# source .venv/bin/activate      # Linux/Mac
pip install -r requirements.txt
alembic upgrade head             # 应用数据库迁移
uvicorn app.main:app --reload    # 启动 API，访问 http://localhost:8000/docs

# 5. Celery worker（新终端，用于异步任务）
cd backend
.venv\Scripts\activate
celery -A app.workers.celery_app worker --loglevel=info

# 6. 前端（新终端）
cd frontend
npm install
npm run dev                      # 访问 http://localhost:5173
```

### 4.3 验证安装

打开 http://localhost:5173，应该能看到 Dashboard。点 "Go to Workspaces" → "New Workspace" 创建一个 → 上传一篇 PDF → 观察 Parse 列从 `pending` → `parsing` → `parsed (N chunks)`。

如果 Parse 列一直停在 `pending`，说明 Celery worker 没启动或 Redis 没跑——看 [progress_and_roadmap.md](progress_and_roadmap.md)（如果你能访问）或问 yx。

---

## 五、必读文档清单

按你的角色读不同文档：

### 所有队友都读

1. **[plans.md](plans.md)** — 项目核心规划、MVP 范围、产品原则
2. **[data_contracts.md](data_contracts.md)** — **最关键**，四人数据交付契约
3. **本 ONBOARDING.md** — 你正在读

### yx 自己读（不上传，内部）

- code_guide.md
- progress_and_roadmap.md

### zwx（RAG）额外读

- [db_schema.md](db_schema.md) — `knowledge_items` / `knowledge_relations` / `evidence_spans` 表结构，GraphRAG 会用到
- [api_reference.md](api_reference.md) — "Retrieval（计划）"章节，了解 `semantic_search` / `find_similar_work` / `find_counter_evidence` 接口契约
- [data_contracts.md](data_contracts.md) 的 **Contract #1（chunks，你的输入）和 Contract #4（RetrievalResult，你的输出）**

### zf（微调）额外读

- [data_contracts.md](data_contracts.md) 的 **Contract #2（knowledge_items，你的训练目标）+ Contract #3（训练样本格式）+ Contract #5（opportunity_candidates，第二阶段微调输入）+ Contract #6（LLM provider 部署要求）**
- [GapMind Benchmark plan.md](GapMind%20Benchmark%20plan.md) — 评估指标，你的微调模型要对比 Deepseek baseline
- [db_schema.md](db_schema.md) — `knowledge_items.content` 的 5 种 type schema，你的微调模型必须输出严格符合这些 schema 的 JSON

### ygl（Execute + Analyze）额外读

- [data_contracts.md](data_contracts.md) 的 **Contract #2（knowledge_items，你的 Analyze 要消费）+ Contract #4（RetrievalResult，对比已有工作要调）+ 待定义的 #7/#8/#9（你负责起草 #8/#9）**
- [db_schema.md](db_schema.md) — `artifacts` / `timeline_events` / `knowledge_items` 表结构，你设计的 `experiments` 数据模型要和它们对齐
- [api_reference.md](api_reference.md) — "Artifact" 和 "Knowledge" 章节，了解现有 API 模式
- [plans.md](plans.md) — P1（Execute）和 P2（Analyze）的产品定位与边界，特别是"不自动运行任意 GitHub 仓库"的约束

---

## 六、你的工作清单（按角色）

### zwx（RAG）的 Week 1-4

```
Week 1-2：
  □ 读 data_contracts.md Contract #1 + #4
  □ 与 yx 一起定 Milvus collection schema（30 min 评审会）
  □ 实现 Milvus collection 创建 + insert + 基础 search
  □ 实现 semantic_search() 函数
  □ 构造 20-30 个 query 的评估集
  □ 跑 baseline 指标

Week 3-4：
  □ 实现 find_similar_work() 和 find_counter_evidence()
  □ 调优（reranker / hybrid search / query expansion）
  □ 评估指标对比
  □ 交付：app/domains/retrieval/service.py 里的 3 个函数实现 + 评估报告

Week 5+（可选 GraphRAG）：
  □ 利用 knowledge_items + knowledge_relations 做图增强检索
  □ 评估 GraphRAG vs 普通 RAG
```

### zf（微调）的 Week 1-7

```
Week 1-2（数据准备，和 yx 一起）：
  □ 读 data_contracts.md Contract #2 + #3 + #6
  □ yx 用 Deepseek 跑 Smoke Set 抽取
  □ 你和 yx 一起修正 100-200 个 chunks 的抽取结果（gold 标注）
  □ 转换为 ShareGPT/Alpaca 格式训练集
  □ 评估集：留出 20-30 个样本不参与训练
  □ 确认基座模型（Qwen3:8B）+ 框架（LLaMA-Factory 或 Axolotl）

Week 3-4（抽取模型微调）：
  □ 用 QLoRA 跑 Qwen3:8B LoRA 微调
  □ 评估：和 Deepseek zero-shot / few-shot 对比
  □ 输出 adapter weights + 评估报告

Week 5-6（Opportunity 微调，需要 Phase 5 就绪）：
  □ yx 用 Discover Agent 跑出 100+ candidates
  □ 导师 + 你 + yx 评分（6 维度，见 Contract #5）
  □ 构造 DPO/SFT 数据
  □ 微调 + 评估

Week 7+（部署集成）：
  □ 用 vLLM 部署微调模型（OpenAI 兼容 API）
  □ yx 的 Model Gateway 接入
  □ 端到端测试
```

### ygl（Execute + Analyze）的 Week 1-7

> ygl 的工作集中在 P1/P2 阶段（Phase 7 和后续），前期可以不投入，专注读文档 + 理解架构。Week 5+ 开始正式开发。

```
Week 1-2（理解项目，不投入开发）：
  □ 读 ONBOARDING.md + plans.md + data_contracts.md
  □ 重点理解 Step ⑩→⑪→⑫ 的上下游：Plan 输出什么、Execute 消费什么、Analyze 产出什么
  □ 本地跑通环境（见第四章），上传一篇论文走完 Phase 2 流程，理解 chunks 长什么样
  □ 和 yx 一起过一遍 Contract #7（research_plan）草案，理解 Baseline Checklist 的字段

Week 3-4（数据模型设计 + Contract 起草）：
  □ 设计 `experiments` 数据模型：是新建表还是用 `knowledge_items` type=`experiment`？和 yx 评审
  □ 起草 Contract #8: experiment_record（实验日志/指标/图表的格式）
  □ 起草 Contract #9: analysis_report（分析报告 + Finding 的格式）
  □ 评审通过后更新到 data_contracts.md

Week 5-6（Execute 实现，Phase 7 启动）：
  □ 实现 `experiments` API（CRUD + 状态机：not_started/in_progress/completed/failed）
  □ 实现 Baseline Checklist 跟踪界面（前端）
  □ 实现实验产物上传（logs/metrics/plots 作为 artifact）
  □ Timeline 事件 `experiment.*` 接入
  □ 关键约束：不自动跑 GitHub repo，只提供 Checklist 跟踪 + 产物上传

Week 7+（Analyze 实现）：
  □ 实现 Analyze LLM 调用流程（通过 yx 的 Model Gateway）
  □ 接入 zwx 的 `semantic_search` 做对比检索
  □ 输出分析报告 + `knowledge_items` type=`finding`
  □ 更新 Hypothesis 状态：confirmed / rejected / partial
  □ 如果 zf 有 Analyze 微调模型，接入替换 Deepseek
```

---

## 七、协作约定

### 代码仓库约定

- 所有代码进同一个 Git 仓库
- zwx 的 RAG 代码进 `backend/app/domains/retrieval/`
- zf 的训练脚本进 `training/`（新建目录），微调后的 adapter 放 `models/`（git LFS 或外部存储，**不进 git**）
- ygl 的 Execute/Analyze 代码进 `backend/app/domains/experiment/` 和 `backend/app/domains/analysis/`（新建）
- 评估脚本进 `evaluation/`
- **每次提交写有意义的 commit message**

### 数据交付约定

- 所有跨人数据走 `data/` 目录（见 [data_contracts.md](data_contracts.md) 的"数据存储位置约定"章节）
- 交付前跑验证脚本（Phase 2 之后会提供 `scripts/validate/`）
- **不合规的数据不接受**——队友有权要求你修格式

### 沟通约定

- **每周一次 sync**（建议周五，30-60 分钟），过接口对齐 + 下周计划
- 日常用微信/Slack 群，重要决策写文档
- **任何 schema 字段变更必须先改 data_contracts.md 再通知**——不要私下改字段名

### 关键同步点

| 时间点 | 同步内容 | 参与者 |
|--------|---------|--------|
| Week 1 末 | Milvus schema 评审（30 min） | yx + zwx |
| Week 2 末 | chunks 格式确认 + 首批数据交付 | yx + zwx + zf |
| Week 3 初 | KnowledgeItem content schema 评审（1h） | yx + zf + 导师 |
| Week 3-4 | experiments 数据模型 + Contract #8/#9 起草评审 | yx + ygl |
| Week 4 末 | 训练样本质量评审 | yx + zf |
| Week 5 末 | RetrievalResult 集成测试 | yx + zwx |
| Week 5-6 | Execute 实现 sync（Checklist 跟踪 + 产物上传） | yx + ygl |
| Week 6 末 | Opportunity candidate 评分会议 | yx + zf + 导师 |
| Week 7 末 | 微调模型集成测试 | yx + zf |
| Week 7+ | Analyze 实现 sync（对比检索 + Finding 产出） | yx + ygl + zwx |

---

## 八、当前项目状态（2026-07-21）

### ✅ 已完成

- **Phase 0**：基础设施搭建（PostgreSQL + Redis + Milvus + FastAPI + Celery + React 前端）
- **Phase 1a**：Workspace 完整 CRUD + 前端集成
- **Phase 1b**：核心 Domain 数据层（Artifact / Paper / Task / Timeline / Knowledge）+ API
- **Phase 1b+**：PDF metadata 自动提取 + 补传 PDF + 元数据编辑
- **Phase 2**：PDF 解析 Pipeline（PyMuPDF 全文提取 + 启发式章节识别 + 三级分块 + Celery 异步任务 + chunks JSONL 导出）

### 🚧 进行中 / 待做

- **Phase 3**：Knowledge 抽取 + Milvus 检索（zwx 主要工作阶段）
- **Phase 4**：HITL 工作流 + Timeline 完善
- **Phase 5**：Discover Agent（旗舰能力）+ Plan 生成
- **Phase 6**：前端完善 + 知识图谱可视化
- **Phase 7**：P1 功能（Execute / Baseline Reproduction Checklist）
- **后续**：P2（Analyze / Publish）+ P3（Respond）

### Phase 与 Step 对应关系

| Phase | 覆盖的 Step | 优先级 |
|-------|-------------|--------|
| Phase 0-2（已完成） | ① ② | P0 |
| Phase 3 | ③ ④ ⑤ ⑥ ⑦a ⑦b | P0 |
| Phase 4 | ⑨（HITL 完善） | P0 |
| Phase 5 | ⑧ ⑨ ⑩（部分） ⑦c | P0/P1 |
| Phase 6 | 前端完善 + 图谱可视化 | P0 |
| Phase 7 | ⑩ ⑪ | P1 |
| 后续 | ⑫ ⑬ ⑭ | P2/P3 |

### 当前能做什么

- ✅ 创建 Workspace、上传 PDF、自动解析为 chunks
- ✅ 查看 Timeline（paper.uploaded + paper.parsed 事件）
- ✅ Tasks 区显示解析任务 + 进度
- ❌ 还不能做 Semantic Scholar 论文搜索（Step ③，Phase 2-3）
- ❌ 还不能做语义检索（Step ⑤，Phase 3）
- ❌ 还不能抽取结构化知识（Step ⑥，Phase 3）
- ❌ 还不能发现 Research Opportunity（Step ⑧，Phase 5）
- ❌ 还不能生成 Research Plan / Baseline Checklist（Step ⑩，Phase 5-7）
- ❌ P2/P3 的 Analyze / Publish / Respond 暂不在 MVP 范围

---

## 九、遇到问题怎么办

| 问题 | 找谁 |
|------|------|
| 环境搭建失败（Docker / Python / Node） | 先看 [README.md](../README.md)，再问 yx |
| 数据库迁移失败 | yx |
| API 调用报错 | yx（看 [api_reference.md](api_reference.md)） |
| chunks 格式不符合 Contract #1 | yx |
| Milvus 连接问题 | zwx（Phase 3 后） |
| 训练数据格式问题 | zf + yx |
| 微调模型部署 | zf |
| experiments 数据模型设计 | ygl + yx |
| Baseline Checklist 字段含义 | ygl + yx |
| Analyze 对比检索接入 | ygl + zwx |
| Contract #8/#9 起草 | ygl |
| 不清楚自己该做什么 | 看本 ONBOARDING.md 第六章 + 问 yx |

---

## 十、术语表

| 术语 | 含义 |
|------|------|
| **Workspace** | 研究工作空间，所有数据的 scope 边界 |
| **Artifact** | 通用文件记录（PDF / parsed_text / chunk_index / report） |
| **KnowledgeItem** | 结构化知识对象，17 种类型（Phase 1b 支持 7 种） |
| **EvidenceSpan** | 证据回链，指向论文原文的具体文本片段 |
| **Opportunity** | Research Opportunity，GapMind 的旗舰产出物 |
| **HITL** | Human-in-the-Loop，AI 提议 + 人类确认 |
| **Chunk** | PDF 解析后的文本块（约 512 token） |
| **Contract** | 数据契约，见 [data_contracts.md](data_contracts.md) |
| **Contract #1-#6** | 见第三章流程图的 6 个衔接点 |
| **Smoke Set** | 8-12 篇 GNN 论文，早期开发验证用 |
| **Development Set** | 30-40 篇论文，正式 LoRA 训练用 |

---

欢迎加入 GapMind！有问题随时问 yx。
