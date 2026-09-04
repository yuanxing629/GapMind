# GapMind 论文证据 GraphRAG 与 Neo4j 分阶段升级方案

> 文档日期：2026-09-03
> 文档状态：待实施方案
> 适用范围：Workspace Chat/RAG、Knowledge Graph、论文阅读、证据溯源、检索评测与可观测性
> 前置基线：`docs/0903_knowledge_graph_workspace_refactor_plan.md`

## 1. 执行摘要

GapMind 的数据结构和产品目标确实适合 GraphRAG，但适合的原因不是“有一张知识图谱就必须使用图数据库”，而是论文之间存在可解释的共享结构：规范方法、任务、数据集、论文来源、论文级 claim/limitation、PaperMention 和 EvidenceSpan 可以共同组成受约束的研究证据图。

本方案采用以下结论：

1. PostgreSQL 继续作为业务事实和权限边界的唯一真源。
2. Milvus 继续作为论文段落的向量召回索引。
3. 先在 PostgreSQL 上实现有边界的 GraphRAG 检索投影，采用“向量种子 + 图扩展 + 证据回检 + 受控生成”。
4. 先以 shadow/诊断模式验证 GraphRAG 是否改善跨论文、多跳问题，不能仅凭架构偏好引入 Neo4j。
5. Neo4j 如果引入，只作为可重建、可回滚、只读的图投影加速层，不承担业务写入、权限判断或证据真源职责。
6. 现有论文视角、claims 视角、evidence 视角和单篇论文图谱必须保留；Workspace 总览作为知识图谱默认入口。
7. claim 和 limitation 始终保持论文上下文，不能因为名称、文本或 embedding 相似就自动合并。

最终目标不是让模型“沿着任意边自由联想”，而是让每一个跨论文结论都能回答以下问题：

```text
这个结论经过了哪些图路径？
路径涉及哪些论文？
具体证据段落在哪里？
哪些节点是人工确认事实，哪些只是 AI 抽取候选？
当前回答是否仍然属于本 Workspace？
```

## 2. 当前基线与约束

### 2.1 当前系统组成

GapMind 当前采用：

| 能力 | 当前组件 | 在本方案中的位置 |
| --- | --- | --- |
| 业务数据与租户边界 | PostgreSQL + SQLAlchemy | 唯一事实源和权限过滤源 |
| 论文段落检索 | Milvus + embedding/reranker | 向量种子召回和证据段落回检 |
| 知识抽取 | Celery + LLM | 生成抽取候选，不能直接成为人工确认事实 |
| 知识图谱 | `KnowledgeItem`、`KnowledgeRelation`、`CanonicalEntity`、`PaperMention`、`EvidenceSpan` | 受约束的图投影和证据导航 |
| Workspace 对话 | Chat domain + citation/source passport | GraphRAG 的回答编排和引用一致性 |
| 前端图谱 | React、TypeScript、Cytoscape | Workspace 总览和下钻交互 |
| 任务队列 | Redis + Celery | 异步索引、投影刷新和可选 Neo4j 同步 |
| 外部图数据库 | 当前没有 | 只在评测证明必要后作为可选读投影 |

### 2.2 当前知识对象的语义边界

现有对象必须按下面的语义使用：

| 对象 | 范围 | 允许的 GraphRAG 用法 |
| --- | --- | --- |
| `Paper` | Workspace 内的论文 | 研究来源、回答引用和下钻入口 |
| `KnowledgeItem` | 论文级 | 表达该论文抽取出的方法、任务、数据集、claim、limitation 等条目 |
| `CanonicalEntity` | Workspace 级 | 连接跨论文共享的规范 method/task/dataset |
| `PaperMention` | 论文级提及 | 表达论文如何提及某个规范实体，并回链原文位置 |
| `EvidenceSpan` | 论文级证据 | 回链到 Markdown/PDF 的具体证据范围 |
| `KnowledgeRelation` | 当前为 KnowledgeItem 到 KnowledgeItem | 论文内部语义关系；只有语义安全、来源明确时才可投影到实体层 |
| `claim` | 论文上下文 | 必须保持每篇论文独立，不自动合并 |
| `limitation` | 论文上下文 | 必须保持每篇论文独立，不自动合并 |

目前 `CanonicalEntity` 的唯一性由 `workspace_id + type + normalization_key` 保证，创建范围主要是 method、task、dataset。这是第一阶段可以依赖的跨论文聚合键。

### 2.3 已有 Workspace 图谱改造应作为基线

`docs/0903_knowledge_graph_workspace_refactor_plan.md` 已定义并部分实现 Workspace 图谱投影。后续 GraphRAG 不应重新定义这套前端或 API 语义，而应在其上增加检索投影和证据路径能力：

- Workspace 默认展示 paper 和 canonical_entity。
- 聚合边保留 occurrence、paper、evidence、supporting ids 等统计。
- PaperMention/EvidenceSpan 按需展开。
- 单篇论文和证据视角继续保留。
- 返回边的 source/target 必须存在于当前响应节点。
- `paper_id` 严格筛选不能因为共享实体而隐式加载其他论文；关联论文必须是显式模式。

### 2.4 不可改变的工程约束

1. 所有查询必须包含 `workspace_id`，并过滤软删除数据。
2. 不删除现有知识数据、现有 API 或视角。
3. 不在前端通过 `label`、`canonical_name` 或展示文本合并节点。
4. 不把 AI 抽取候选显示为已经人工确认的科学事实。
5. 不让图路径代替原文证据；GraphRAG 只能帮助找证据，不能凭图边生成无证据事实。
6. Chat 同步和 SSE 调用继续遵循现有 LLM 兼容约定，结构化调用传 `disable_thinking=True`，不注入厂商专属 `reasoning_effort`。
7. OpenAPI 变更后使用 `npm run gen:api` 更新类型，不手写 `frontend/src/api/types/api.gen.ts`。
8. 不在本阶段把 Neo4j 作为请求链路的硬依赖。

## 3. 为什么采用 GraphRAG，为什么不立即采用 Neo4j

### 3.1 GraphRAG 对 GapMind 的实际价值

论文检索不仅是“找一段相似文本”，还包括：

- 找到多篇论文共同使用的方法。
- 比较同一方法在不同任务或数据集上的结果。
- 从任务找到方法，再找到使用该方法的论文和原文证据。
- 将一篇论文的 limitation 与其他论文的相关方法或实验设置关联起来。
- 发现跨论文的研究组合，但仍保留每条 claim 的原始论文身份。

这些问题天然需要“文本相关性 + 结构关系 + 证据回链”。GraphRAG 可以对向量召回的结果做受限扩展，补充单段文本相似度无法稳定召回的跨论文连接。

### 3.2 GraphRAG 不等于图数据库

GraphRAG 是检索和上下文组织策略；Neo4j 是一种图存储和查询实现。GapMind 已经在 PostgreSQL 中拥有：

- Workspace 外键和索引。
- 规范实体唯一性。
- 论文级知识条目。
- 提及、关系和证据回链。
- SQLAlchemy 事务和软删除规则。

因此，初始 GraphRAG 完全可以通过 PostgreSQL 的 bounded join、预聚合查询或受限递归 CTE 实现。先验证检索收益，可以减少双写、同步、部署、备份和租户隔离方面的额外风险。

### 3.3 Neo4j 的额外复杂度

如果直接引入 Neo4j，系统会同时维护两套数据表示：

```text
PostgreSQL 业务真源 ──同步/重建──> Neo4j 图投影
          │                         │
          └── Milvus 向量索引       └── GraphRAG 查询
```

额外复杂度至少包括：

- Neo4j 服务的本地和部署运维。
- 驱动、连接池、配置、健康检查和测试替身。
- PostgreSQL 到 Neo4j 的首次全量构建和增量同步。
- 事务提交成功但投影同步失败时的一致性处理。
- 节点和边软删除后的清理或版本化。
- 每个节点、每条边的 Workspace 隔离校验。
- 重新抽取、人工确认、实体合并后的幂等更新。
- 备份、恢复、索引和版本兼容。
- PostgreSQL、Milvus、Neo4j 三个外部依赖同时不可用时的降级矩阵。

所以本方案不是否定 Neo4j，而是把 Neo4j 的引入条件改为可验证的工程决策：只有当 PostgreSQL 图投影在真实数据上出现明确的性能或查询表达能力瓶颈，并且 GraphRAG 评测已证明值得继续投入时，才进入 Neo4j 阶段。

## 4. 产品目标和非目标

### 4.1 产品目标

1. Workspace Chat 默认支持论文证据增强，而不是只做纯文本相似召回。
2. 跨论文、多跳问题能够利用规范实体和论文关系找到更多相关证据。
3. 每个回答仍然可以回链到论文、EvidenceSpan 或可定位的原文段落。
4. 用户可以从 Workspace 图谱的实体节点查看来源论文，再进入论文阅读页。
5. 用户能够区分人工确认条目和 AI 抽取候选。
6. 精确论文问题、单篇论文问题和证据溯源问题不因 GraphRAG 引入而退化。
7. 查询、图谱、聊天和证据都严格限定在当前 Workspace。
8. 图谱和检索都渐进式加载，避免把所有节点、PaperMention、EvidenceSpan 一次性放到画布或上下文。

### 4.2 非目标

本轮不做：

- 直接把所有知识条目迁移到 Neo4j。
- 以 Neo4j 取代 PostgreSQL。
- 让 LLM 自由生成未经过证据回检的图路径事实。
- 自动合并 claim 或 limitation。
- 把所有低置信度候选纳入默认 GraphRAG 扩展。
- 通过图路径绕过已有 Workspace 权限和来源排除规则。
- 用机械指标替代人工判断“证据是否足够”。
- 用一轮离线评测就把 GraphRAG 设为不可回滚的默认实现。
- 删除现有 `all`、`landscape`、`claims`、`evidence` 或论文图谱接口。

## 5. 目标图语义契约

### 5.1 节点类型

GraphRAG 使用的逻辑节点建议分为四层：

#### A. Workspace 总览节点

- `paper`：论文来源和下钻入口。
- `canonical_entity`：跨论文共享的规范 method/task/dataset。

#### B. 论文上下文节点

- `knowledge_item`：单篇论文内的抽取条目。
- `claim`：论文级观点。
- `limitation`：论文级局限。

#### C. 证据节点

- `paper_mention`：论文对规范实体的一次提及或提及聚合。
- `evidence_span`：原文证据范围。
- `chunk`：向量检索使用的论文分块，若对外暴露必须带来源护照。

#### D. 检索运行节点

这些不是持久化业务节点，而是一次查询的临时结果：

- seed：向量召回种子。
- path：受限图路径。
- evidence candidate：路径关联的证据候选。

### 5.2 边类型与安全等级

建议给边定义安全级别，而不是仅凭字符串关系名决定是否扩展：

| 边 | 来源 | 默认 GraphRAG 扩展 | 说明 |
| --- | --- | --- | --- |
| `paper_mentions_entity` | PaperMention | 是 | 论文与规范实体的明确关联 |
| `entity_used_by_paper` | 上述边反向 | 是 | 只作为查询方向的投影 |
| `entity_entity_semantic` | KnowledgeRelation 的安全投影 | 条件允许 | 必须有明确关系类型和原始 item 来源 |
| `item_relation` | KnowledgeRelation | 论文视角可用 | 不默认跨论文传播 |
| `item_has_evidence` | EvidenceSpan | 证据回检时使用 | 不能被当作语义事实 |
| `mention_has_evidence` | PaperMention/EvidenceSpan | 证据回检时使用 | 需要精确 paper_id |
| `paper_contains_item` | KnowledgeItem | 论文视角可用 | 用于回链和过滤 |
| `canonicalizes` | 抽取/归一化过程 | 默认不显示 | 技术性边，不等于科学语义关系 |
| `claim_similar_to_claim` | 相似度推断 | 默认禁止 | 相似不代表可以合并或互相支持 |
| `limitation_similar_to_limitation` | 相似度推断 | 默认禁止 | 保持论文上下文 |

### 5.3 状态和可信度

任何节点或边在进入 GraphRAG 上下文时都必须携带来源状态。至少区分：

- `confirmed`：经过人工确认，允许进入默认事实型上下文。
- `extracted_candidate`：AI 抽取候选，默认只能作为候选线索，不能写成已确认事实。
- `rejected`：不得进入默认检索。
- `deleted`：软删除后不得进入检索或图谱。

若现有字段名称不同，应在投影层做清晰映射，不要改变历史数据的语义。对于一个由多个 item 聚合出的实体或边，只能同时提供：

1. 聚合统计。
2. 支撑论文和 item ID。
3. 可用的 evidence ID。
4. 聚合状态说明。

聚合后的实体不能被标记为比其来源更高的确认状态。

### 5.4 claim/limitation 的强制规则

以下行为禁止：

```text
论文 A 的 claim “方法有效”
论文 B 的 claim “方法无效”
                 ↓ 文本相似
        合并为一个跨论文 claim
```

正确做法是：

```text
论文 A ──contains──> claim A
论文 B ──contains──> claim B
claim A / claim B 可被检索到同一问题下
但回答必须保留各自 paper_id、原文和立场
```

如果未来需要“争议观点”节点，也必须新增明确的数据语义和人工审核流程，不能通过当前 GraphRAG 投影隐式实现。

## 6. 目标检索架构

### 6.1 总体流程

```text
用户问题
   ↓
请求范围校验：workspace_id、来源排除、显式论文上下文
   ↓
轻量问题类型识别
   ├─ 精确/单篇论文问题 ──> 现有 dense 主路径
   ├─ 跨论文/比较问题 ──> dense seed + graph expansion
   └─ 图谱实体问题 ─────> entity seed + evidence retrieval
   ↓
Milvus 召回论文段落或实体 seed
   ↓
PostgreSQL 图投影查询，最多扩展 1 到 2 跳
   ↓
按论文和证据回检相关 chunks/EvidenceSpan
   ↓
合并、去重、rerank、按论文分散
   ↓
构建带图路径和来源护照的上下文
   ↓
LLM 生成答案
   ↓
[En] 与 source passport 一致性校验
   ↓
持久化回答、检索审计、证据引用和 GraphRAG 诊断字段
```

### 6.2 不能走图的默认规则

GraphRAG 不应成为所有问题的强制路径。以下情况优先使用现有 dense 路径：

- 用户指定了单篇论文。
- 用户要求原文定位、页码、章节或精确数字。
- 问题只涉及一个明确的知识条目。
- 图谱没有足够的 confirmed 或可回链候选。
- 图扩展返回的证据质量低于 dense 结果。
- 图服务异常、超时或投影版本不一致。

发生异常时，系统应降级到 dense 检索，并在 `retrieval_audit` 中记录降级原因，不能静默把“没有图结果”说成“没有相关研究”。

### 6.3 种子召回

种子优先级：

1. 用户显式指定的论文、实体或知识条目。
2. Milvus 返回的论文 chunks。
3. Workspace 图谱搜索返回的 canonical entity。
4. 受控的 claim/limitation 文本匹配。

种子必须带：

- `workspace_id`。
- `paper_id` 或明确的 workspace-level entity ID。
- 来源类型。
- 原始检索分数和 rank。
- 关联的 chunk/evidence ID（如果有）。
- 查询请求 ID。

不能把搜索结果中仅有的 `label` 或标题作为持久化身份。

### 6.4 图扩展边界

初始版本建议只允许：

- 从 chunk 或 item 找到其 paper。
- 从 paper 找到其 confirmed 或合规 candidate 的 canonical entity。
- 从 canonical entity 找到支撑论文。
- 在有明确语义类型、同一 Workspace 和可回链 item 的条件下，投影少量 entity-to-entity 关系。
- 从实体或 item 回检证据 span/chunk。

默认限制：

- 最大跳数 1 到 2。
- 最大扩展节点数由配置控制。
- 高度节点按论文分散和关系安全级别截断。
- 单论文最多贡献可配置数量的证据段落。
- 不跨 Workspace 连接。
- 不从纯 `canonicalizes` 技术边继续扩展。
- 不以 claim/limitation 相似边扩展。
- 没有 evidence 回链的语义边只能作为诊断候选，不能直接进入事实性回答。

这些限制的目的不是压低召回，而是避免图扩展将一个常见实体连接到大量无关论文，导致上下文污染和回答不可审计。

### 6.5 图路径对象

一次检索的图路径建议使用临时 DTO，不新增持久化表。建议字段：

```json
{
  "path_id": "path:request-id:1",
  "workspace_id": "workspace-id",
  "nodes": [
    {"id": "paper:1", "kind": "paper"},
    {"id": "entity:1", "kind": "canonical_entity"}
  ],
  "edges": [
    {
      "id": "edge:mention:1",
      "type": "paper_mentions_entity",
      "source": "paper:1",
      "target": "entity:1",
      "paper_id": "paper:1",
      "supporting_item_ids": ["item:1"],
      "supporting_evidence_ids": ["evidence:1"]
    }
  ],
  "supporting_paper_ids": ["paper:1"],
  "supporting_evidence_ids": ["evidence:1"],
  "review_status": "confirmed"
}
```

图路径是检索解释和证据导航对象，不是新的科学事实表。路径中任何节点和边都必须再次经过 Workspace 和软删除校验。

### 6.6 上下文打包

发送给 LLM 的上下文至少分为三部分：

1. `Evidence excerpts`：可引用的原文段落，带 `[En]` 序号和 paper/chunk/evidence 身份。
2. `Graph relations`：只描述“检索到的结构线索”，每条带 supporting IDs，不把推断写成事实。
3. `Scope and uncertainty`：当前 Workspace、候选状态、证据数量和不足说明。

模型提示词必须明确：

- 只能使用提供的证据回答事实性问题。
- 必须保留论文之间的差异和冲突。
- 不得因为同一规范实体就合并 claim/limitation。
- 没有证据时说明不足，不得用图边补写原文不存在的结论。
- 所有 [En] 引用必须对应真实的 evidence/chunk 条目。

## 7. PostgreSQL-first 实施设计

### 7.1 第一阶段不新增表

初始实现只增加只读投影和检索服务，不改变现有知识表的基本语义。建议新增或拆分的代码职责如下，最终文件名以实施时的代码审查为准：

| 位置 | 职责 |
| --- | --- |
| `backend/app/domains/knowledge/` | 图谱投影、实体聚合、受限路径查询 |
| `backend/app/domains/chat/` | GraphRAG 编排、上下文打包、回答审计 |
| `backend/app/domains/retrieval/` | dense seed、chunk 回检、rerank 和论文分散 |
| `backend/app/core/config.py` | feature flag、限额、超时和降级配置 |
| `backend/app/workers/tasks/` | 可选异步投影刷新和评测任务 |
| `evaluation/` | baseline、GraphRAG A/B 和人工复核数据 |
| `frontend/src/api/` | 自动生成 API 类型和 GraphRAG 诊断 DTO |

除非真实数据证明预聚合必要，不新增 graph_nodes、graph_edges 或同步 outbox 表。临时路径、计数和截断元数据应在请求内计算，并通过已有字段或明确的新响应模型返回。

### 7.2 查询实现建议

第一版优先使用明确的 SQLAlchemy 查询和 bounded join：

- 先按 Workspace、软删除和状态过滤。
- 先确定 seed ID 集合，再查询关联实体、论文和证据。
- 使用固定的 hop/limit，不执行无界递归。
- 对聚合统计使用分组查询，避免逐节点 N+1 查询。
- 查询结束后做一次端点完整性校验，过滤没有返回 source/target 的边。
- 将 `truncated`、`has_more` 和具体原因记录在响应和审计中。

只有确实需要多跳层级查询且 bounded join 变得难以维护时，才考虑受限递归 CTE。递归查询必须设置最大深度、最大结果集和 Workspace 条件。

### 7.3 图投影与聊天投影分离

Workspace 图谱接口解决“用户看见什么”；GraphRAG 检索接口解决“回答需要什么”。两者可以共用实体聚合函数，但不能让前端图谱返回的节点集合直接充当 LLM 上下文：

- 图谱默认只返回 paper/canonical_entity。
- Chat 可以按问题展开 item/mention/evidence/chunk。
- Chat 的扩展上限可以小于或不同于画布上限。
- GraphRAG 返回的证据必须经过 Chat 的 source passport 和 citation consistency 流程。

### 7.4 版本和缓存

初始阶段不需要复杂缓存。若后续需要缓存：

- key 必须包含 `workspace_id`、projection version、query scope 和 filter。
- 依赖数据的最大 `updated_at` 或 extraction run version。
- 任何实体确认、拒绝、软删除、重抽取都能使相关缓存失效。
- 缓存失效失败时优先返回 PostgreSQL 实时结果。

不能使用跨 Workspace 的全局实体缓存。

## 8. API 升级方案

### 8.1 现有知识图谱 API

保留现有知识图谱接口和所有视角，在其上继续使用 `projection_mode=workspace` 作为 Workspace 总览入口。新改动应保证：

- `workspace` 不再依赖 KnowledgeItem 分页才能正确表示共享实体。
- 默认返回 paper 与 canonical_entity，并排除全部 PaperMention/EvidenceSpan。
- 聚合节点和边的统计字段完整。
- 所有返回边的两个端点都出现在本次 `nodes`。
- 严格 `paper_id` 模式不会加载共享实体关联的其他论文；关联论文必须通过显式参数或显式下钻动作请求。
- `has_more` 和 `truncated` 说明的是节点、边或展开结果哪一部分被截断。

### 8.2 Chat API 的兼容扩展

建议在现有 `ChatMessageCreate` 上以可选字段增加诊断和灰度能力，不破坏旧客户端：

```text
retrieval_mode: dense | hybrid | graph
graph_expand: boolean
graph_max_hops: integer
graph_node_limit: integer
graph_edge_limit: integer
```

具体字段是否全部对外开放，应根据现有产品需要裁剪。推荐的默认策略：

- 对旧请求保持 `dense` 或当前既有行为。
- 通过服务端 feature flag 先进入 `hybrid_shadow`，只记录 GraphRAG 结果，不改变回答。
- 评测通过后，Workspace 跨论文问题才允许 `hybrid`。
- `graph` 纯图模式只用于调试或明确的实体探索，不作为普通 Chat 默认模式。

响应可增加可选的 `graph_context` 或 `retrieval_audit.graph`：

- `mode`。
- `seed_count`。
- `expanded_node_count`。
- `expanded_edge_count`。
- `path_count`。
- `supporting_paper_ids`。
- `supporting_evidence_ids`。
- `truncated` 和 `truncation_reason`。
- `fallback` 和 `fallback_reason`。

不要在默认消息正文中直接渲染内部节点 ID。用户可见内容仍以论文、证据和可读关系为主。

### 8.3 证据接口

证据回链继续使用已有 evidence context 能力。GraphRAG 新增字段必须能映射回：

- `paper_id`。
- `artifact_id`。
- `chunk_id`。
- `evidence_span_id` 或等价 ID。
- 起止字符位置或章节信息。
- 原始论文标题和可读标签。

当原始 PDF/Markdown 不可用时，前端应显示“证据暂不可用”，不能把图边或摘要当作原文替代物。

## 9. Neo4j 可选阶段设计

### 9.1 进入条件

只有同时满足下面条件，才进入 Neo4j 实施：

1. PostgreSQL GraphRAG 已完成 shadow 和真实 Workspace 评测。
2. 已确认至少一类多跳问题存在稳定收益，而不是单个样例收益。
3. 已确认 bounded SQL 查询在真实数据规模下产生可重复的 p95 延迟或维护瓶颈。
4. 已定义同步失败、重建、软删除、权限过滤和回滚方案。
5. 用户明确接受增加一个基础设施组件及其运维成本。

不能因为“未来数据可能变大”就提前把 Neo4j 作为硬依赖。

### 9.2 Neo4j 的角色

Neo4j 只能作为：

- 从 PostgreSQL 重建的只读图投影。
- GraphRAG 的可选扩展查询引擎。
- 图谱调试和路径可视化的加速存储。

Neo4j 不负责：

- 业务事实最终写入。
- 用户和 Workspace 权限判定。
- 人工确认状态的最终存储。
- EvidenceSpan 原文存储。
- 代替 Milvus 的段落向量检索。

### 9.3 建议配置

如果正式进入 Neo4j 阶段，配置可以独立于 LLM 配置增加：

```dotenv
NEO4J_ENABLED=false
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=
NEO4J_DATABASE=neo4j
NEO4J_SYNC_MODE=rebuild
NEO4J_PROJECTION_VERSION=v1
```

默认必须关闭。密码不能写入仓库，`.env.example` 只能保留空值或占位说明。配置加载失败不得影响 dense 检索和 PostgreSQL 图投影。

### 9.4 数据投影规则

建议使用以下节点身份：

```text
(:Workspace {workspace_id})
(:Paper {paper_id, workspace_id})
(:CanonicalEntity {entity_id, workspace_id, type, normalization_key})
(:KnowledgeItem {item_id, paper_id, workspace_id, type})
(:PaperMention {mention_id, paper_id, workspace_id})
(:EvidenceSpan {evidence_id, paper_id, workspace_id})
```

每个关系都必须带 `workspace_id` 或能通过两端校验得出同一 Workspace。推荐使用源表的稳定 ID，禁止按显示 label 生成身份。

投影过程：

1. 按 Workspace 从 PostgreSQL 读取未软删除数据。
2. 生成 projection version 和构建时间。
3. 使用稳定 ID `MERGE` 节点和关系。
4. 对确认、候选、拒绝状态做显式属性映射。
5. 校验关系两端的 Workspace、源表 ID 和软删除状态。
6. 在临时版本构建并通过校验后切换可读版本。
7. 记录构建统计、失败原因和最后成功版本。

初始同步方式建议为 Celery 触发的按 Workspace rebuild。只有当重建成本成为现实瓶颈，才考虑 outbox 或按事件增量同步。不要在用户请求的数据库事务中同步写 Neo4j。

### 9.5 一致性和故障策略

Neo4j 投影允许最终一致，但回答不能读取“版本未知”的图：

- PostgreSQL 数据更新后，Neo4j 可能暂时落后。
- 请求发现投影版本落后或构建失败时，回退到 PostgreSQL GraphRAG。
- Neo4j 连接失败、查询超时或结果校验失败时，回退到 PostgreSQL/dense。
- 回退情况写入检索审计。
- 回退期间不能返回旧 Workspace 的缓存结果。

### 9.6 Neo4j 测试要求

不要求普通单元测试连接真实 Neo4j。应提供：

- driver mock/fake。
- projection export fixture。
- 幂等 rebuild 测试。
- 软删除后不再返回测试。
- 两个 Workspace 交叉隔离测试。
- 关系端点完整性测试。
- Neo4j 不可用时的 PostgreSQL fallback 测试。
- projection version 不匹配测试。

真实 Neo4j 集成测试可以作为独立 CI job 或本地可选测试，不应成为默认后端测试套件的必需外部依赖。

## 10. 前端升级方案

### 10.1 Workspace 图谱交互

默认视角保持 `workspace`，主画布只显示：

- 论文节点。
- canonical entity 节点。
- 论文到实体的聚合边。
- 经过安全投影的少量实体语义边。

默认关闭全部关系标签，只有选中或悬停时显示关系和统计。技术性 `canonicalizes` 边、全部 PaperMention 和 EvidenceSpan 不进入初始画布。

实体节点必须显示或能快速看到：

- 实体名称、类型。
- `paper_count`。
- `mention_count`。
- `knowledge_item_count`。
- `evidence_count`。
- `confirmed_item_count`。
- 别名。
- 来源论文数量或摘要。

### 10.2 Inspector/Drawer

点击 canonical entity 后，Inspector 或移动端 Drawer 显示：

- 名称、类型和规范化身份。
- aliases。
- 覆盖论文数。
- 提及次数。
- 知识条目数。
- 证据数。
- 人工确认数。
- 关联论文列表。
- 每篇关联论文的入口。
- “展开证据”动作。
- “进入论文视角”动作。

点击 paper 后：

- 进入论文视角或论文阅读页。
- 保留 Workspace ID。
- 不把关联实体所属的其他论文默默加入严格论文视角。

### 10.3 搜索和渐进式加载

搜索到当前画布外的节点时必须明确区分：

```text
搜索结果存在，但尚未加载到当前画布
    ↓ 点击定位
请求该节点的焦点子图
    ↓
只合并合法节点和端点完整的边
    ↓
将画布聚焦到目标节点
```

前端不能因为搜索结果包含某个节点就假装该节点已经存在于 Cytoscape。`mergeGraph`、focus、branch 和 projection 函数都必须按稳定 ID 工作，不得按 label 合并。

渐进式加载包括：

- Workspace 初始摘要。
- 搜索节点焦点子图。
- 选中实体后展开关联论文。
- 选中论文后进入论文视角。
- 证据节点按需加载。

### 10.4 Chat 证据展示

GraphRAG 不能在前端只展示“路径 A -> B -> C”。应同时展示：

- 这条路径关联的论文。
- 论文段落或 evidence span。
- 节点/边的确认状态。
- 截断提示。
- 回链原文按钮。

当路径只有候选关系而没有支持证据时，展示为“候选关联”，不能使用“已证实”措辞。

### 10.5 移动端

移动端默认只保留一个主画布和可关闭 Drawer：

- 图谱节点统计使用短标签或二级信息。
- Drawer 支持上下滑动和关闭。
- 论文阅读页的批注栏继续可隐藏/展开。
- 证据回链按钮保持可点击区域足够大。
- 不要求移动端一次展示完整图路径。

## 11. 评测方案与上线门槛

### 11.1 必须建立的对照组

至少比较：

1. Dense-only：当前向量检索基线。
2. SQL Graph-only：仅图种子/图扩展。
3. Dense + SQL Graph：向量种子加 PostgreSQL 图扩展。
4. Dense + Neo4j：仅在 Neo4j 阶段实施后比较。
5. Dense fallback：图查询失败或证据不足时的降级路径。

GraphRAG 不能只在少数人工挑选样例上展示。数据集应同时包含：

- 单篇论文事实问题。
- 跨论文比较问题。
- 方法、任务、数据集多跳问题。
- claim/limitation 对比问题。
- 需要原文证据定位的问题。
- 无答案或证据不足的问题。
- Workspace 隔离攻击/交叉查询问题。

### 11.2 指标

检索层：

- seed recall@k。
- evidence recall@k。
- supporting paper recall。
- 多跳路径有效率。
- 端点完整率。
- 论文分散度。
- 图扩展节点/边数量。
- p50/p95 延迟。

回答层：

- 引用正确率。
- 引用覆盖率。
- 证据支持率。
- 跨论文比较正确率。
- 论文间冲突保留率。
- 未支持事实率。
- `insufficient_evidence` 判断质量。

产品和安全层：

- Workspace 隔离必须是 100% 通过。
- 软删除对象不得出现在结果中。
- 关系端点完整率必须是 100%。
- 证据回链可用率。
- GraphRAG 故障降级成功率。
- 用户对“来源论文”和“候选关联”的理解正确率。

### 11.3 评测原则

现有离线经验表明 dense 检索在当前基线上不能被未经验证地替换；此前 dense-only 曾优于某些 faceted 变体，且 k=20 基线召回良好。这只能作为需要重新验证的历史线索，不是当前版本的永久结论。

因此：

- 固定 Gold Set 不得为过评测而随意修改。
- 真实 Chat QA 和人工复核必须与机械指标同时存在。
- `k=15` 或类似 top-k 只作为诊断观察点，不能单独作为生产门槛。
- `insufficient_evidence` 必须人工确认，不能仅由计数规则决定。
- “GraphRAG 路径更多”不等于“回答更好”。
- 只有在 exact/single-paper 问题不明显退化、跨论文多跳问题出现稳定收益、证据和隔离完整的条件下，才考虑扩大默认范围。

建议把“多跳证据召回提升至少 10 个百分点、精确问题无明显退化、p95 在产品预算内”作为待审批的候选门槛，而不是未经负责人确认的硬性生产规则。阈值应在当前真实 Workspace 上重新校准，并保留人工 verdict。

## 12. 分阶段实施路线

### Phase 0：契约冻结与基线记录

目标：不改变回答，只把边界和可观测性准备好。

工作项：

1. 阅读并确认当前 knowledge graph workspace projection。
2. 固定节点、边、状态和 evidence passport 契约。
3. 为 Chat retrieval audit 增加 GraphRAG 可选诊断结构。
4. 记录 dense-only 的真实 QA、延迟和证据指标。
5. 建立 GraphRAG 评测样例，尤其是多跳问题和 claim/limitation 对比。
6. 增加 workspace isolation、soft delete、端点完整性的测试夹具。

验收：

- 旧 API、旧视角和旧 Chat 行为不变。
- 能对一次请求记录 graph disabled/shadow 的原因。
- Gold、观察结果和人工 verdict 的文件语义明确区分。

### Phase 1：PostgreSQL GraphRAG shadow

目标：计算 GraphRAG 候选，但不改变用户看到的最终答案。

工作项：

1. 增加 graph seed 适配器。
2. 增加 Workspace bounded expansion。
3. 聚合 paper/entity/item/evidence 关系。
4. 对路径做 workspace、软删除、状态和端点校验。
5. 回检支持路径的 chunks/EvidenceSpan。
6. 将 GraphRAG 和 dense 的候选集合、证据、延迟写入诊断。
7. 确认 GraphRAG 查询失败时不影响 dense 答案。

验收：

- GraphRAG 结果中所有边端点存在。
- 不出现其他 Workspace。
- claim/limitation 不合并。
- 结果可逐条回链论文和证据。
- shadow 结果可以离线重放和人工审查。

### Phase 2：混合检索灰度

目标：只对明确适合的跨论文问题使用 GraphRAG 上下文。

工作项：

1. 增加 `dense`、`hybrid`、必要时 `graph` 的可选诊断模式。
2. 使用轻量规则或受控分类识别跨论文问题，不让意图路由静默创建持久任务。
3. 对 hybrid 上下文执行 evidence re-retrieval 和 rerank。
4. 保持旧引用格式和 source/citation consistency。
5. 在前端展示路径关联的论文和证据，而不是内部图数据库细节。
6. 通过 feature flag 按 Workspace 或用户灰度。

验收：

- 多跳问题的人工复核优于 dense 基线。
- 精确问题不会因不必要扩展而变差。
- 降级、超时和证据不足均可解释。
- 用户能区分候选关联和已确认事实。

### Phase 3：Neo4j 可选读投影

目标：在 PostgreSQL 图投影已经证明有价值且出现规模/查询瓶颈后，验证 Neo4j 是否值得增加。

工作项：

1. 增加可选 Docker Compose profile 和环境配置，默认关闭。
2. 建立按 Workspace rebuild/export 流程。
3. 建立稳定 ID、版本、幂等 MERGE 和端点校验。
4. 以 mock driver 覆盖同步和失败测试。
5. 在真实数据上比较 PostgreSQL 与 Neo4j 的查询延迟、扩展规模和维护成本。
6. Neo4j 失败时自动回退到 PostgreSQL GraphRAG。

验收：

- PostgreSQL 仍是唯一写入和权限真源。
- Neo4j 可以从 PostgreSQL 完整重建。
- 两个 Workspace 的数据在投影和查询中都不会互串。
- 旧服务在 Neo4j 关闭时仍可启动和工作。
- Neo4j 方案相对 SQL 方案有明确、可复现的收益。

### Phase 4：按数据证据决定默认实现

只有通过 Phase 2/3 的评测和人工审查，才决定：

- 继续使用 PostgreSQL GraphRAG。
- 将 hybrid 设为 Workspace Chat 默认。
- 让 Neo4j 成为特定规模 Workspace 的可选加速层。
- 暂缓 Neo4j，继续优化 dense、reranker、top-k 和 evidence packing。

该阶段必须保留 feature flag 和回滚开关至少一个发布周期。

## 13. 测试清单

### 13.1 后端知识图谱测试

必须覆盖：

- 同一 Workspace 多篇论文引用同一规范实体时只返回一个 canonical entity。
- 同名但不同 type 的实体不合并。
- 不同 Workspace 的相同实体不合并、不互相可见。
- claim 和 limitation 即使文本相同也保持不同 item 和 paper 上下文。
- 严格 `paper_id` 模式不混入其他论文 PaperMention。
- 关联论文模式只有在显式参数下启用。
- 聚合节点统计和聚合边统计正确。
- 所有返回边的 source/target 都存在于节点集合。
- node/edge limit、`has_more`、`truncated` 和 truncation reason 正确。
- 软删除 paper、item、mention、entity、evidence 后不出现在图谱或检索中。
- 搜索未加载节点时返回可构建焦点子图的稳定 ID。

### 13.2 后端 GraphRAG 测试

- dense seed 严格带 Workspace 过滤。
- 图扩展最大 hop 生效。
- 高度节点被截断且有诊断信息。
- 没有证据回链的边不会直接成为事实性上下文。
- candidate/confirmed/rejected 状态的扩展规则正确。
- claim/limitation 不通过名称、文本或向量相似自动合并。
- evidence re-retrieval 可以回到原始 chunk/artifact。
- graph failure 回退到 dense。
- GraphRAG 查询不改变旧 Chat 的 citation consistency 行为。
- sync/SSE 使用一致的 GraphRAG 策略和诊断字段。

### 13.3 Neo4j 可选测试

- Neo4j 关闭时后端启动成功。
- export/rebuild 幂等。
- projection version 不匹配时安全回退。
- 软删除和人工状态变更能反映到下一次 rebuild。
- Workspace 隔离在 driver mock 和真实集成测试中都通过。
- Neo4j 超时或连接错误不泄露旧缓存，不阻断 dense。

### 13.4 前端测试

- `GraphViewMode` 默认是 workspace。
- Workspace 投影只显示允许的节点和边。
- 不按 label 合并同名不同 ID 节点。
- 实体统计信息在 Inspector/Drawer 正确展示。
- 搜索到未加载节点时显示“定位/加载焦点子图”，不假装已在画布中。
- 论文点击能进入论文视角或阅读页，并保留 workspace_id。
- strict paper mode 不显示关联论文；关联论文由显式操作触发。
- 默认不显示全部关系标签，选中/悬停可见。
- 截断状态有清晰提示。
- evidence 点击可以回链原文或显示不可用状态。
- 移动端 Drawer、论文阅读和证据按钮可操作。

### 13.5 验证命令

后端：

```powershell
cd D:\MyCode\Spark-competition\refactor\GapMind\backend
.venv\Scripts\python.exe -m pytest tests/test_knowledge_api.py -q
.venv\Scripts\python.exe -m pytest tests/ -q
```

前端：

```powershell
cd D:\MyCode\Spark-competition\refactor\GapMind\frontend
npm run gen:api
npm test -- --run
npm run typecheck
npm run build
npm run lint
```

如果后端 OpenAPI 没有变化，不需要为了 GraphRAG 方案文档重复生成类型；实际 API 修改后必须生成并检查 diff，不能手写 `api.gen.ts`。

## 14. 文件级实施清单

新会话开始实施前，先重新检查当前 HEAD，不要假设文件内容仍与方案编写时完全相同。建议检查：

### 后端

- `backend/app/core/config.py`：feature flag、限额、超时、Neo4j 默认关闭配置。
- `backend/app/domains/knowledge/models.py`：现有节点和关系语义。
- `backend/app/domains/knowledge/schemas.py`：图投影和路径 DTO。
- `backend/app/domains/knowledge/service.py`：Workspace 聚合、严格论文过滤、bounded expansion。
- `backend/app/domains/knowledge/router.py`：兼容 API 参数和响应。
- `backend/app/domains/chat/schemas.py`：检索模式和 graph audit 可选字段。
- `backend/app/domains/chat/service.py`：seed、扩展、证据回检、上下文打包和 fallback。
- `backend/app/domains/chat/router.py`：同步和 SSE 参数传递。
- `backend/app/domains/retrieval/service.py`：Milvus 召回、rerank、论文分散和证据定位。
- `backend/app/workers/tasks/extract_knowledge.py`：抽取状态、实体/关系更新和投影刷新触发点。
- `backend/tests/test_knowledge_api.py`、`backend/tests/test_chat_api.py`：API/隔离/引用回链。

### 前端

- `frontend/src/components/KnowledgeGraph.tsx`：视角、搜索、展开、Inspector/Drawer 和焦点子图。
- `frontend/src/components/knowledgeGraph/graphUtils.ts`：稳定 ID 投影、合并、分支、端点完整性。
- `frontend/src/api/knowledge.ts`：知识图谱请求参数。
- `frontend/src/api/types/knowledge.ts`：非生成的领域辅助类型。
- `frontend/src/api/types/api.gen.ts`：只由 `npm run gen:api` 更新。
- Chat 页面和证据组件：GraphRAG 路径、论文、证据和降级提示。
- `frontend/src/components/layout/navigation.ts`、论文阅读页面：保持从图谱到论文阅读的入口。
- `frontend/src/components/knowledgeGraph/graphUtils.test.ts` 及新增交互测试。

### 评测与基础设施

- `evaluation/`：固定 Gold、真实 QA、GraphRAG 对照组和人工 verdict。
- `infra/docker-compose.yml`：仅在 Neo4j 阶段增加可选 profile，默认不启动。
- `.env.example`：只放空的 Neo4j 占位配置，不放真实密钥。
- README/运行文档：说明 PostgreSQL-first、fallback 和 Neo4j 可选性质。

## 15. 风险与处理

| 风险 | 表现 | 处理 |
| --- | --- | --- |
| 常见实体导致图扩展爆炸 | 上下文变大、无关论文增多 | 限制 hop/degree、按论文分散、按证据排序 |
| 图边被误认为事实 | 回答引用路径而非原文 | 强制 evidence re-retrieval 和 source passport |
| candidate 被当成 confirmed | 前端/回答过度肯定 | 状态显式展示，默认事实上下文优先 confirmed |
| claim/limitation 被错误合并 | 论文观点冲突消失 | 永远保留 paper_id 和 item_id，禁止自动合并 |
| 共享实体带来论文筛选串线 | 用户以为 Workspace 数据错误 | strict/related 模式显式区分，默认 strict |
| Neo4j 与 PostgreSQL 不一致 | 返回过期或跨租户数据 | PG 真源、版本校验、重建、失败回退 |
| GraphRAG 没有真实收益 | 增加复杂度但回答不变好 | shadow、固定 Gold、真实 QA 和人工 verdict |
| 前端一次加载过多 | Cytoscape 卡顿、移动端不可用 | 搜索/展开/分支焦点子图和服务端上限 |
| LLM 过度推断 | 生成来源不存在的结论 | prompt 约束、引用一致性、证据不足拒答/降级 |

## 16. 完成定义

GraphRAG 第一阶段只有在以下条件全部满足时才算完成：

1. PostgreSQL-first 的 GraphRAG shadow 路径已实现。
2. dense-only 旧路径仍可用并且可配置回退。
3. Workspace、软删除、strict paper 和 candidate/confirmed 边界均有测试。
4. claim/limitation 未被自动合并。
5. 图路径、边端点、论文、item 和 evidence ID 可审计。
6. 同步和 SSE 的检索审计字段一致。
7. 前端可以从 Workspace 实体下钻到论文和原文证据。
8. 搜索未加载节点不会伪装成当前画布节点。
9. 截断、降级、证据不足和候选状态均有清晰 UI 提示。
10. 后端和前端验证命令通过，或明确记录环境阻塞而不把阻塞误判为功能通过。
11. 没有在未评测前把 Neo4j 设为硬依赖。

Neo4j 阶段只有在完成定义之外再满足“可重建、可回退、可隔离、可度量，并相对 PostgreSQL 方案有可复现收益”后才算完成。

## 17. 新会话实施顺序

新会话应按照下面顺序操作：

1. 阅读 `AGENTS.md`、本方案和 Workspace 知识图谱方案。
2. 查看 `git status --short`、最近提交、当前 migration head 和测试基线。
3. 重新检查 knowledge、chat、retrieval、worker、frontend graph 和现有评测文件。
4. 先补 GraphRAG 契约与后端测试，再实现 bounded PostgreSQL projection。
5. 先接入 shadow audit，不要直接改变默认回答。
6. 使用固定 QA 和真实 Workspace 做 dense/hybrid 对照。
7. 评测通过后再做 feature-flag 灰度、前端路径展示和渐进式展开。
8. 只有明确证明 SQL 图投影出现瓶颈，才实现 Neo4j 可选读投影。
9. 每次 API 变更后生成前端类型并运行完整验证。
10. 修改代码后先测试和汇报，不要自行 push；提交仍需用户明确授权。

## 18. 最终决策摘要

GapMind 应该升级为“证据约束的混合 GraphRAG”，而不是直接升级为“Neo4j 驱动的全图问答”：

```text
PostgreSQL 真源
      +
Milvus dense seed
      +
受限的实体/论文图扩展
      +
EvidenceSpan/chunk 回检
      +
source passport 与引用一致性
      ↓
可解释、可回滚、可验证的论文 GraphRAG
```

Neo4j 是后续可能的查询加速器，不是本次升级的前提。先把图的语义边界、证据链、Workspace 隔离和评测闭环做对，再根据真实规模和 p95 数据决定是否承担第二套图存储的复杂度。
