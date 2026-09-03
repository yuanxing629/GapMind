# Workspace 级知识图谱改造方案

> 文档日期：2026-09-03
> 文档状态：待实施方案
> 适用模块：Knowledge Workbench、Knowledge Graph、论文与证据溯源
> 实施原则：先复用现有数据模型，先增加只读投影，再逐步替换默认展示，不立即引入新的图数据库或大规模数据迁移。

## 1. 背景与问题定义

当前知识图谱页面虽然通过 `workspace_id` 查询，但图谱的核心节点仍然是单篇论文抽取出来的 `KnowledgeItem`。用户看到的主要是：

```text
论文 A → A 的方法、任务、数据集、观点、局限
论文 B → B 的方法、任务、数据集、观点、局限
论文 C → C 的方法、任务、数据集、观点、局限
```

这种展示方式适合查看一篇论文内部的抽取结果，但不适合回答 Workspace 层面的研究问题：

- 哪些方法在多篇论文中反复出现？
- 哪些任务和数据集形成了稳定的研究组合？
- 哪些论文使用了相同的方法，但研究结论不同？
- 哪些实体连接了多个论文子图？
- 某个研究观点或局限来自哪些论文？

因此，本次改造的目标不是简单地把每篇论文的图拼接成一张大图，而是建立一个以 Workspace 为范围、以规范实体为跨论文连接点、以论文和证据为下钻路径的分层知识图谱。

## 2. 改造结论

### 2.1 总体结论

建议将 Workspace 级研究全景作为知识图谱的默认视图，同时保留单篇论文视角和证据溯源视角。

推荐的最终结构如下：

```text
Workspace 研究全景
  ├── 论文节点
  ├── 跨论文共享的规范实体节点
  ├── 聚合后的论文与实体关系
  └── 统计信息：覆盖论文数、提及数、证据数、确认数
          ↓ 点击节点
跨论文详情或单篇论文视角
          ↓ 点击知识条目
证据原文与人工审核
```

### 2.2 不建议的做法

不建议直接展示以下全量内容：

- 所有 `KnowledgeItem`
- 所有 `PaperMention`
- 所有 `EvidenceSpan`
- 所有低置信度抽取结果
- 所有关系标签

原因是这会把“研究地图”和“抽取审计数据”混在一起，导致节点爆炸、标签重叠、布局不稳定，并且用户难以判断哪些连接是跨论文事实，哪些只是抽取过程中的中间关系。

### 2.3 数据模型结论

现有数据模型已经具备 Workspace 聚合的基础，不需要第一阶段就新增图数据库或重建所有知识表：

- `CanonicalEntity` 是 Workspace 级实体身份。
- `PaperMention` 保存论文中的实体提及及证据位置。
- `KnowledgeItem` 保存论文级知识条目。
- `KnowledgeRelation` 保存知识条目之间的语义关系。
- `EvidenceSpan` 保存知识条目到原文的证据回链。

第一阶段应新增“图谱投影”，而不是改变这些表的基本语义。

## 3. 现有代码审查结果

### 3.1 当前 API 已经是 Workspace scope，但投影仍以论文级条目为中心

知识图谱接口为：

```text
GET /api/v1/workspaces/{workspace_id}/knowledge/graph
```

路由和服务层都会接收 `workspace_id`，并过滤软删除记录，说明租户隔离基础是正确的。

主要入口：

- `backend/app/domains/knowledge/router.py`
- `backend/app/domains/knowledge/service.py`
- `backend/app/domains/knowledge/schemas.py`

但是，`graph_projection()` 首先查询 `KnowledgeItem`，再以这些条目为主体附加论文、实体和提及节点。因此当前图谱是“Workspace 范围内的论文级知识条目投影”，不是“Workspace 级实体聚合投影”。

### 3.2 `KnowledgeItem` 天然是论文级对象

`KnowledgeItem` 具有 `paper_id`，并且知识抽取任务按单篇论文执行。相同名称的知识条目会因为来自不同论文而拥有不同的 item id。

这意味着不能通过简单地把所有 `KnowledgeItem` 放进同一张画布来实现跨论文图谱。这样只会得到许多同名节点，而不是一个共享实体节点。

### 3.3 现有规范实体可以作为跨论文聚合键

`CanonicalEntity` 的唯一性约束是：

```text
workspace_id + type + normalization_key
```

因此，同一个 Workspace 中相同类型、相同规范化名称的实体可以复用同一实体身份。

当前抽取任务只为以下类型创建规范实体：

- `method`
- `task`
- `dataset`

这是合理的第一阶段范围。`claim` 和 `limitation` 通常依赖论文上下文，不能仅凭文本相似或名称相同就自动合并。

### 3.4 当前前端已经暴露出实体层重复问题

前端的 `hideEntityLayer()` 会默认隐藏 `canonicalizes` 边和孤立的规范实体节点。代码注释明确提到：多个同名知识条目指向一个同名规范实体时会产生视觉噪声。

这说明现有图谱已经遇到以下结构问题：

```text
Method A item 1 ─┐
Method A item 2 ─┼→ Method A canonical entity
Method A item 3 ─┘
```

当前做法是隐藏规范实体层，而不是把它升级为 Workspace 级主视图。因此跨论文共享关系反而被隐藏了。

### 3.5 当前分页会造成“部分全局图”

前端初始只请求 40 个核心节点，之后通过“继续加载节点”追加。后端根据 `KnowledgeItem` 分页，再附加结构节点。

当前行为可能导致：

- 初始画布只包含部分论文。
- 不在同一批次中的相关知识条目无法连边。
- 节点总数和当前可见节点数容易被用户混淆。
- 使用 force layout 时，新增批次会导致整体布局变化。

因此，Workspace 总览不应直接沿用当前的 KnowledgeItem 分页策略。

### 3.6 当前论文筛选需要澄清语义

当前构建提及节点时，会加载：

```text
当前选择的论文中的提及
或
与当前论文共享规范实体的其他论文提及
```

这适合“查看关联上下文”，但不适合名称为“来源论文”的严格筛选器。改造时必须区分：

- 仅查看该论文
- 查看该论文及其共享实体关联论文

否则用户选择一篇论文后看到其他论文，会认为存在数据隔离问题。

## 4. 产品目标与非目标

### 4.1 目标

1. 用户打开知识图谱时，首先看到 Workspace 的研究结构，而不是某一篇论文的抽取明细。
2. 同一个 Workspace 中相同的规范方法、任务和数据集只展示一个共享实体节点。
3. 用户可以看到共享实体覆盖了多少篇论文，并能定位到具体论文和证据。
4. 用户可以从 Workspace 总览下钻到论文级图谱，再下钻到证据原文。
5. 大图保持可读，避免一次性渲染所有原始节点。
6. 所有聚合关系都保留来源论文、知识条目和证据回链。
7. 不改变 Workspace 数据隔离和现有人工审核边界。

### 4.2 非目标

本阶段不做以下事情：

- 不引入 Neo4j、NebulaGraph 等新的图数据库。
- 不把 `claim` 和 `limitation` 按名称自动合并。
- 不删除现有单篇论文图谱。
- 不删除 `KnowledgeItem`、`PaperMention` 或 `EvidenceSpan`。
- 不把 AI 抽取候选自动升级为人工确认事实。
- 不让图谱替代 PDF 阅读和证据审核。
- 不用视觉上的边连接代替真实证据。
- 不为了追求大图数量而取消服务端上限。

## 5. 目标信息架构

建议将知识图谱组织为三个主要视角，并让 Workspace 视角成为默认视角。

### 5.1 Workspace 研究全景

用途：回答“这个课题空间整体研究了什么”。

默认节点：

- 论文
- 规范方法
- 规范任务
- 规范数据集

默认不显示：

- `PaperMention`
- `EvidenceSpan`
- 所有低置信度节点
- 所有技术性结构边

建议节点信息：

```json
{
  "id": "entity:{entity_id}",
  "node_kind": "canonical_entity",
  "label": "GNNExplainer",
  "entity_type": "method",
  "paper_count": 8,
  "mention_count": 26,
  "knowledge_item_count": 14,
  "evidence_count": 22,
  "confirmed_item_count": 4,
  "confidence": 0.91,
  "review_status": "extracted_candidate"
}
```

建议节点的视觉权重优先考虑：

1. 覆盖论文数。
2. 人工确认条目数。
3. 证据数量。
4. 关系数量。
5. 置信度。

不要只用置信度决定节点大小。高置信度但只出现一次的实体，不一定比跨越多篇论文的实体更重要。

### 5.2 论文视角

用途：查看某篇论文的知识结构。

保留现有的论文级图谱能力：

- 论文
- 方法
- 任务
- 数据集
- 观点
- 局限
- 论文内部语义关系

该视角可以继续使用 `KnowledgeItem` 作为主要节点，但需要严格保证选择论文后只加载该论文的数据。若用户需要查看共享实体的其他论文，应通过明确的“查看关联论文”动作进入，而不是隐式混入。

### 5.3 证据溯源视角

用途：回答“这个节点或关系的证据在哪里”。

可以保留现有细粒度链路：

```text
论文 → 原文提及 → 规范实体 → 知识条目 → EvidenceSpan → Markdown/PDF
```

该视角适合审核和调试，不适合作为默认首页图谱。

## 6. 目标数据语义

### 6.1 节点身份规则

所有跨论文合并必须使用数据库身份，不得在前端按显示名称合并：

```text
canonical_entity_id 是唯一聚合键
canonical_name 只用于展示
normalization_key 只用于后端规范化查找
```

前端不能使用以下方式合并节点：

- `label` 相同
- `canonical_name` 相同
- 去空格后相同
- 大小写不敏感后相同

这些规则可能把不同语义的实体错误合并。

### 6.2 规范实体的适用范围

第一阶段只聚合已有 `CanonicalEntity` 的类型：

- 方法
- 任务
- 数据集

观点和局限保持论文级，不因名称相同自动合并。后续如果需要跨论文观点聚合，应单独设计人工审核、证据一致性和版本机制。

### 6.3 关系语义规则

当前 `KnowledgeRelation` 是知识条目到知识条目的关系。如果要将其投影到规范实体之间，必须明确这是“聚合投影”，不是新事实。

例如：

```text
论文 A 的 Method X → evaluates_on → Dataset Y
论文 B 的 Method X → evaluates_on → Dataset Y
```

Workspace 视图可以展示：

```text
Method X → 在数据集上评估 → Dataset Y
```

但边上必须保留：

- 支持该边的论文数量。
- 支持该边的论文 ID。
- 原始 KnowledgeItem ID。
- 证据数量。
- 最高或聚合后的置信度。

如果关系端点无法安全映射到规范实体，则只在论文视角展示，不强行投影到 Workspace 实体图中。

### 6.4 状态规则

规范实体的存在不等于实体语义已经被人工确认。界面需要区分：

- AI 提取的规范实体。
- 有证据支持的实体。
- 至少一个知识条目已经人工确认。
- 已拒绝或失效的关联。

不能因为实体节点是唯一节点，就在界面上给用户造成“该实体已经被验证”的感觉。

## 7. 后端改造方案

### 7.1 API 方案

建议在现有图谱接口上增加新的投影模式：

```text
GET /api/v1/workspaces/{workspace_id}/knowledge/graph?projection_mode=workspace
```

保留现有模式以兼容旧功能：

- `all`
- `landscape`
- `claims`
- `evidence`

不建议直接改变 `all` 的语义，因为可能影响现有页面、测试和调用方。前端默认切换到 `workspace`，旧模式作为详细视角继续使用。

### 7.2 Workspace 投影的查询流程

建议服务层新增独立方法，例如：

```python
workspace_graph_projection(
    workspace_id,
    paper_ids=None,
    entity_types=None,
    relation_types=None,
    status_filter=None,
    min_confidence=None,
    query_text=None,
    node_limit=80,
    edge_limit=160,
    offset=0,
)
```

查询流程：

1. 验证 Workspace 存在，并由路由层完成所有权检查。
2. 查询 Workspace 内未删除、符合筛选条件的论文。
3. 查询这些论文关联的规范实体。
4. 按 `canonical_entity_id` 聚合 `KnowledgeItem`、`PaperMention`、`EvidenceSpan`。
5. 生成论文节点和规范实体节点。
6. 生成论文到实体的聚合边。
7. 对可安全映射的 `KnowledgeRelation` 生成实体到实体的聚合边。
8. 应用服务端节点和边上限。
9. 返回截断信息和完整统计信息。

### 7.3 Workspace 节点建议字段

建议新增专用响应模型，而不是让现有 `KnowledgeGraphNodeRead` 承担所有语义。可以新增：

```python
class WorkspaceGraphNodeRead(BaseModel):
    id: str
    label: str
    node_kind: Literal["paper", "canonical_entity"]
    type: str
    workspace_id: str
    paper_id: str | None = None
    canonical_entity_id: str | None = None
    confidence: float = 0.0
    review_status: str | None = None
    paper_count: int = 0
    mention_count: int = 0
    knowledge_item_count: int = 0
    evidence_count: int = 0
    confirmed_item_count: int = 0
    aliases: list[str] = []
    supporting_paper_ids: list[str] = []
    supporting_paper_ids_truncated: bool = False
```

如果为了减少 API 类型变化而复用现有模型，至少要保证这些字段有明确含义，不能让 Workspace 聚合节点继续伪装成单个 `KnowledgeItem`。

### 7.4 Workspace 边建议字段

建议新增聚合边模型，或扩展边模型但标记投影版本：

```python
class WorkspaceGraphEdgeRead(BaseModel):
    id: str
    source: str
    target: str
    relation_type: str
    display_label: str | None = None
    confidence: float = 0.0
    occurrence_count: int = 0
    paper_count: int = 0
    evidence_count: int = 0
    supporting_paper_ids: list[str] = []
    supporting_item_ids: list[str] = []
    payload: dict[str, Any] = {}
```

推荐的 Workspace 投影关系类型：

| relation_type | 含义 |
|---|---|
| `paper_entity` | 论文涉及某个规范实体 |
| `entity_relation` | 多篇论文中的知识关系聚合到实体之间 |
| `paper_claim` | 论文包含观点或局限，建议在论文视角或焦点视图使用 |

`paper_entity` 的展示文案应使用“涉及实体”或“论文提及”，不要直接使用技术性的 `canonicalizes`。

### 7.5 聚合规则

对于规范实体 `E`：

```text
paper_count = 去重后的关联 paper_id 数量
mention_count = 未删除 PaperMention 数量
knowledge_item_count = 未删除 KnowledgeItem 数量
evidence_count = 关联 EvidenceSpan 数量
confirmed_item_count = status == human_confirmed 的条目数量
```

对于论文到实体边：

```text
paper_count = 1
occurrence_count = 该论文对该实体的提及或知识条目数量
evidence_count = 该关系能够回链的证据数量
```

对于实体到实体关系：

```text
occurrence_count = 原始 KnowledgeRelation 数量
paper_count = 产生该关系的去重论文数量
confidence = 明确记录聚合规则后的值
```

第一阶段建议保守使用最大置信度和支持论文数量，不要把多个模型置信度简单求平均后称作“事实置信度”。

### 7.6 分页与截断

Workspace 总览使用“重要节点优先”的服务端投影，不使用当前的“KnowledgeItem 按置信度分页”作为唯一分页依据。

建议：

- 默认最多返回 80 个节点、160 条边。
- 用户可通过筛选和搜索缩小范围。
- 节点按覆盖论文数、确认数量、证据数、关系数综合排序。
- 返回 `total_nodes`、`total_edges`、`loaded_nodes`、`loaded_edges`。
- 返回 `has_more` 和 `truncated`。
- 返回 `truncation_reason`，例如 `node_limit` 或 `edge_limit`。
- 如果边的端点不在当前返回节点集合中，必须补齐端点，不能返回悬空边。

首期不要求自动加载全量图。用户需要查看更多内容时，通过搜索、筛选或节点展开完成渐进式加载。

### 7.7 论文筛选语义修正

现有详细视图需要修正为两种明确模式：

1. `paper_id` 严格模式：只加载该论文的论文节点、知识条目、提及和证据。
2. `include_related_papers=true` 关联模式：额外加载与该论文共享规范实体的论文。

不能在没有显式参数的情况下，通过 `paper_id OR canonical_entity_id` 隐式扩大结果集。

### 7.8 邻居接口

现有：

```text
GET /workspaces/{workspace_id}/knowledge/graph/neighbors/{node_id}
```

可以继续复用，但需要支持：

- `entity:{id}` 返回该实体关联的论文、知识条目摘要和关联实体。
- `paper:{id}` 返回该论文的核心实体和高价值观点。
- 返回 `has_more` 或明确的邻居截断信息。
- 所有结果严格按 Workspace 和节点身份校验。

## 8. 前端改造方案

### 8.1 视角切换

在 `KnowledgeGraph.tsx` 和 `graphUtils.ts` 中增加 `workspace` 视角：

```typescript
type GraphViewMode = "workspace" | "landscape" | "claims" | "evidence";
```

推荐显示名称：

- Workspace 总览
- 论文结构
- 观点关系
- 证据溯源

默认值从 `landscape` 改为 `workspace`。现有其他视角保持独立状态，避免切换时丢失用户已经展开的内容。

### 8.2 Workspace 总览画布

默认画布只显示：

- 论文节点。
- 规范实体节点。
- 聚合后的论文到实体边。
- 少量满足条件的实体间关系。

默认隐藏：

- `PaperMention` 节点。
- `canonicalizes` 技术边。
- 全部关系标签。
- 低置信度节点。

如果需要显示这些内容，使用“显示溯源层”或进入证据视角，不在默认画布中打开。

### 8.3 节点详情面板

点击规范实体时，详情面板应优先展示跨论文信息：

1. 实体名称、类型和别名。
2. 覆盖论文数。
3. 提及次数。
4. 关联知识条目数。
5. 有证据条目数。
6. 人工确认条目数。
7. 论文列表。
8. 相关观点和局限摘要。
9. “查看证据”和“进入论文视角”按钮。

点击论文时，详情面板应展示：

- 标题、年份、来源。
- 解析和抽取状态。
- 该论文覆盖的核心实体。
- 该论文的观点和局限数量。
- 进入论文视角按钮。

### 8.4 筛选器调整

Workspace 视角建议保留以下筛选：

- 实体类型：方法、任务、数据集。
- 来源论文。
- 最低覆盖论文数。
- 最低证据数。
- 审核状态。
- 关系类型。

“来源论文”需要增加语义说明或切换：

- 只显示该论文
- 显示该论文关联实体的其他论文

“最低置信度”应在文案中说明它是抽取质量筛选，不代表科学结论已经被验证。

### 8.5 搜索与定位

现有图谱搜索能力可以复用，但搜索结果需要返回：

- 节点类型。
- 节点所属视角。
- 覆盖论文数。
- 代表性论文标题。

搜索命中一个尚未加载的规范实体后，前端不应把它伪装成已经存在于当前画布，而应请求该节点的焦点子图，再定位到该节点。

### 8.6 大图交互

建议使用以下渐进式交互：

```text
默认总览
  → 点击节点查看统计和论文
  → 展开一层关联节点
  → 选择某篇论文进入论文视角
  → 选择某条证据进入原文
```

不建议依赖“继续加载节点”不断向同一画布追加内容。追加加载可以保留作为兜底能力，但主要入口应该是搜索、筛选和节点展开。

### 8.7 布局与可读性

Workspace 总览应避免所有节点使用同一种无约束 force layout。建议：

- 论文节点和实体节点使用明显不同的形状。
- 共享实体按覆盖论文数突出，但避免尺寸过大遮挡标签。
- 关系标签默认隐藏，只在选中或悬停时显示。
- 选中节点时保留一跳邻居，其余节点降低透明度。
- 节点详情放在右侧固定区域或响应式 Drawer 中。
- 移动端默认使用列表加焦点子图，不强行展示全量画布。
- 大图加载时显示节点数、关系数和截断提示。

现有 `Cytoscape.js` 可以继续使用。第一阶段不需要更换渲染引擎，重点是减少进入画布的数据量并改造投影语义。

## 9. 数据质量与人工审核

### 9.1 规范实体合并风险

当前 `normalization_key` 主要解决名称规范化，不等于完整实体消歧。可能出现：

- 相同缩写代表不同方法。
- 同名数据集在不同版本中含义不同。
- 方法名与论文中的变体被错误合并。
- 同一实体的别名没有被识别。

因此，Workspace 视图应允许后续增加：

- 实体合并。
- 实体拆分。
- 别名修正。
- 关联论文解除。

这些操作必须经过人工确认并记录审计信息，不能由前端按名称自动完成。

### 9.2 聚合关系的证据边界

聚合边只能说明“多个论文级记录指向同一投影关系”，不能直接说明科学结论已经成立。

详情面板需要提供：

- 支持论文列表。
- 原始知识条目列表。
- 证据数量。
- 每条证据的来源论文。
- 关系审核状态。

如果聚合边没有可回链证据，应降低展示优先级，或标记为“待核验关系”。

## 10. 实施阶段

### 阶段 0：基线确认

目标：确认现有图谱行为和数据规模。

工作内容：

1. 统计典型 Workspace 的论文数、KnowledgeItem 数、CanonicalEntity 数、PaperMention 数和关系数。
2. 记录当前三种视角的首屏加载时间和画布节点数。
3. 确认当前数据中方法、任务、数据集的规范实体覆盖率。
4. 用两个以上论文包含同一方法的真实数据验证当前重复节点问题。

交付物：基线记录，不修改业务数据。

### 阶段 1：后端 Workspace 聚合投影

目标：在不影响旧视角的情况下提供新的只读 API。

建议修改文件：

- `backend/app/domains/knowledge/schemas.py`
- `backend/app/domains/knowledge/service.py`
- `backend/app/domains/knowledge/router.py`
- `backend/tests/test_knowledge_api.py`

工作内容：

1. 新增 `projection_mode=workspace`。
2. 新增 Workspace 节点和边响应字段。
3. 按规范实体聚合跨论文数据。
4. 增加论文、提及、知识条目、证据和人工确认统计。
5. 增加节点和边上限以及截断元数据。
6. 修正严格论文筛选和关联论文筛选语义。
7. 保证所有边的端点都存在于本次响应。

本阶段原则：不新建表，不删除旧接口，不改变现有 `KnowledgeItem` 的生命周期。

### 阶段 2：前端默认视角切换

目标：让用户默认看到 Workspace 研究全景。

建议修改文件：

- `frontend/src/components/KnowledgeGraph.tsx`
- `frontend/src/components/knowledgeGraph/graphUtils.ts`
- `frontend/src/api/knowledge.ts`
- `frontend/src/api/types/knowledge.ts`
- 后端变更后生成 `frontend/src/api/types/api.gen.ts`
- `frontend/src/components/knowledgeGraph/graphUtils.test.ts`

工作内容：

1. 新增 Workspace 视角类型和展示文案。
2. 默认加载 Workspace 聚合投影。
3. 实体详情展示覆盖论文数、提及数和证据数。
4. 论文节点可进入论文视角。
5. 实体节点可展开关联论文和知识摘要。
6. 默认隐藏提及层和技术性规范化边。
7. 增加截断提示和渐进式加载状态。
8. 维持桌面端右侧 Inspector 和移动端 Drawer。

### 阶段 3：论文视角和证据视角收敛

目标：清晰区分总览、论文内部关系和证据审计。

工作内容：

1. 论文视角严格按 `paper_id` 隔离。
2. 关联论文必须通过显式操作进入。
3. 证据视角继续支持 `PaperMention` 和 `EvidenceSpan`。
4. 统一节点详情中的“进入审核工作台”和“查看证据”入口。
5. 对低置信度、已拒绝和失效数据显示明确状态。

### 阶段 4：数据质量与性能优化

目标：在真实规模 Workspace 上稳定工作。

工作内容：

1. 检查规范实体误合并和漏合并。
2. 增加实体合并、拆分的人工审核方案。
3. 为聚合统计增加必要索引或查询优化。
4. 对 10、50、200 篇论文规模进行测试。
5. 评估 Workspace 图谱缓存，但不提前引入复杂缓存失效系统。
6. 评估 Cytoscape 布局在最大节点数下的稳定性。

## 11. 测试方案

### 11.1 后端单元和 API 测试

至少覆盖：

1. 两篇论文引用同一个规范方法时，Workspace 视图只返回一个实体节点。
2. 同名但不同类型的实体不会合并。
3. 实体节点正确返回覆盖论文数、提及数和证据数。
4. 两篇论文对相同实体关系的聚合边保留论文计数和来源 ID。
5. `claim` 和 `limitation` 不会因为同名自动合并。
6. Workspace A 的实体、论文和关系不会出现在 Workspace B。
7. 严格论文筛选不会加载其他论文的提及。
8. 关联论文模式只在显式参数开启时生效。
9. 节点和边达到上限时返回正确的截断信息。
10. 所有返回边的 source 和 target 都存在于返回节点。
11. 软删除的论文、实体、知识条目、提及和关系不会出现在默认投影。
12. 空 Workspace、没有规范实体、只有观点的 Workspace 都有明确空状态。

### 11.2 前端测试

至少覆盖：

1. Workspace 视角是默认视角。
2. 聚合实体显示论文覆盖数。
3. 选择论文后可以进入论文视角。
4. 默认不显示 `PaperMention`。
5. 搜索未加载节点后可以请求焦点子图并定位。
6. 截断状态有明确提示。
7. 筛选重置后恢复 Workspace 总览。
8. 桌面端 Inspector 和移动端 Drawer 都能展示实体详情。

### 11.3 验证命令

后端：

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_knowledge_api.py -q
.venv\Scripts\python.exe -m pytest tests/ -q
```

前端：

```powershell
cd frontend
npm test -- --run
npm run typecheck
npm run build
npm run lint
```

后端 API Schema 发生变化后：

```powershell
cd frontend
npm run gen:api
```

不得手写 `frontend/src/api/types/api.gen.ts`。

## 12. 验收标准

### 功能验收

- 打开知识图谱默认进入 Workspace 总览。
- 一个跨多篇论文的规范实体只显示一个主节点。
- 实体节点能够显示覆盖论文数和证据统计。
- 用户能够查看该实体出现的具体论文。
- 用户能够进入单篇论文视角。
- 用户能够从知识条目进入证据原文。
- 论文严格筛选不会混入其他论文数据。
- 所有 Workspace 查询都保持用户隔离。

### 可读性验收

- 默认画布不显示所有原文提及。
- 默认画布不显示全部关系标签。
- 初始画布节点数受到服务端上限控制。
- 节点和边不会出现悬空端点。
- 节点选中后，邻居关系能够清晰区分。
- 大于上限的数据通过搜索、筛选和展开继续访问。
- 移动端不要求用户拖动一张完整大图才能查看关键内容。

### 证据验收

- 聚合节点和边可以回链到原始 KnowledgeItem。
- 聚合节点和边可以回链到来源论文。
- 可用的 EvidenceSpan 数量可见。
- AI 提取候选和人工确认状态有明显区分。
- 图谱视觉连接不被当作未经审核的科学结论。

## 13. 风险与应对

### 风险一：错误实体合并

应对：只使用 `canonical_entity_id` 聚合，不在前端按名称合并；后续增加人工合并和拆分机制。

### 风险二：全局图谱仍然过大

应对：Workspace 总览只加载聚合实体和重要论文，服务端限制节点与边；证据和提及按需展开。

### 风险三：聚合关系被误认为事实

应对：边上保留支持论文、知识条目和证据数量；界面使用“聚合关系”或“论文提及”语义，不隐藏证据边界。

### 风险四：现有视角被改坏

应对：新增 `workspace` 投影，不立即改变 `all`、`landscape`、`claims`、`evidence` 旧语义；先增加 API 测试，再切换前端默认视角。

### 风险五：分页导致关系断裂

应对：Workspace 投影由服务端按聚合结果排序，边的端点必须补齐；返回 `truncated` 和统计信息。

### 风险六：数据库查询变慢

应对：第一阶段先使用现有表和索引，限制投影规模；基线确认后再针对 `workspace_id`、`canonical_entity_id`、`paper_id`、软删除字段补充索引。

## 14. 下一会话实施顺序

下一会话开始后，建议严格按照以下顺序执行：

1. 阅读本方案和当前知识图谱相关文件。
2. 检查 `git status --short`，确认当前分支和已提交状态。
3. 运行现有 `test_knowledge_api.py`，建立基线。
4. 先设计并实现后端 `projection_mode=workspace` 的响应模型和服务逻辑。
5. 为跨论文同实体、严格论文筛选、Workspace 隔离和截断编写测试。
6. 运行后端知识图谱测试和完整后端测试。
7. 重新生成 OpenAPI TypeScript 类型。
8. 实现前端 Workspace 总览、实体详情和论文下钻。
9. 增加前端投影函数和交互测试。
10. 运行前端测试、类型检查、构建和 lint。
11. 使用真实 Workspace 手工检查重复实体、空状态、筛选、展开和证据回链。
12. 最后再评估是否需要索引、缓存或数据修复脚本。

## 15. 最终决策

本项目应采用以下最终方向：

```text
Workspace 总览作为默认入口
规范实体作为跨论文共享节点
论文作为研究来源和下钻入口
claim/limitation 保持论文上下文
PaperMention/EvidenceSpan 按需展开
单篇论文图谱和证据视角继续保留
```

这比“每篇论文各自展示”更符合 GapMind 的研究空间定位，也比“把所有节点简单拼成一张巨型图”更适合真实用户阅读、比较和核验。
