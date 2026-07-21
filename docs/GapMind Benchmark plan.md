# GapMind Benchmark Plan

## 1. Benchmark 目标

GapMind 的 MVP 不应只证明“LLM 能总结论文”，而应证明：

1. 系统能够从 AI/CS 文献中提取可靠、可追溯的结构化科研信息；
2. 系统能够把论文、方法、任务、数据集、实验结果、局限性和未来方向组织为知识图谱；
3. 系统能够识别由多篇论文共同支持、同时考虑相似工作或反证的 Research Opportunity；
4. 系统输出的 Opportunity 能被研究者理解、审查、接受、修改或拒绝；
5. Opportunity 可以进一步转化为可编辑的 Research Question、Hypothesis 和 Baseline Reproduction Checklist。

本 Benchmark 服务于三个目标：

- 比赛：证明产品不是“普通 RAG + Chatbot”；
- 工程：验证检索、抽取、图谱、Agent 和 HITL 的每一层是否可靠；
- 后续论文：积累可复现的评测集、人工判断和人机协作数据。

---

## 2. 核心研究问题

### RQ1：结构化抽取是否可靠？

给定 AI/CS 论文，GapMind 是否能准确抽取：

- Research Task；
- Problem Setting；
- Method；
- Method Component；
- Dataset；
- Baseline；
- Metric；
- Experimental Result；
- Limitation；
- Future Work；
- Claim；
- Evidence；
- Code Repository（若有）；
- Citation / Related Work relationship。

### RQ2：系统能否生成有证据支持的 Research Opportunity？

给定一个研究主题和一组相关论文，GapMind 能否生成：

- 不只是某篇论文 Future Work 的复述；
- 有至少多个独立来源支持；
- 包含相似工作、冲突证据或反证；
- 可进一步转化为研究问题与验证计划；
- 对 AI/CS 研究者有价值的 Opportunity。

### RQ3：知识图谱是否改善 Opportunity Discovery？

比较以下不同信息组织方式：

- 仅关键词检索；
- 普通 RAG；
- RAG + LLM 结构化抽取；
- RAG + 结构化知识图谱；
- 后续：RAG + 知识图谱 + GNN 排序。

重点评估知识图谱是否改善：

- 证据覆盖；
- 相似工作发现；
- 反证发现；
- Opportunity 的新颖性判断；
- Opportunity 的可行性判断；
- 输出的可解释性。

### RQ4：Human-in-the-Loop 是否提升可信度和实用性？

评估研究者是否能够：

- 理解 Opportunity 的来源；
- 检查每条关键判断对应的论文证据；
- 识别系统遗漏的相似工作；
- 接受、编辑、延后或拒绝 Opportunity；
- 基于确认的 Opportunity 生成更可行的研究计划。

---

## 3. MVP Benchmark 范围

### 3.1 目标领域

第一版聚焦：

> AI/CS，尤其是 Graph Neural Networks（GNN）与 Self-Interpretable GNN。

选择该领域的原因：

- 团队具备领域专业知识；
- 可以人工判断 Method、Task、Dataset 和实验设置；
- 论文之间具有丰富的引用、方法演化和任务关系；
- 容易构建“相似工作 / 差异 / 局限性 / Opportunity”的高质量测试案例；
- 后续可自然延展到图谱推理和 GNN-based Ranking。

### 3.2 文献规模建议

不要一开始追求大规模。

建议分三层构建：

| 数据集              |   论文数量 | 用途                                                 |
| ------------------- | ---------: | ---------------------------------------------------- |
| Smoke Set           |    8–12 篇 | 验证 PDF 解析、抽取、检索和 UI                       |
| Development Set     |   30–50 篇 | Prompt、抽取 schema、图谱关系和 Opportunity 策略迭代 |
| Evaluation Set      |   20–30 篇 | 固定不参与 Prompt 调优，用于正式展示与对比评测       |
| Future Research Set | 100–300 篇 | 后续图谱分析、GNN、论文实验                          |

MVP 的重点应是 30–50 篇高质量、人工审查过的论文，而不是几千篇未经验证的论文。

### 3.3 论文来源建议

优先来源：

- Semantic Scholar API：搜索、元数据、引用关系、相关论文；
- 论文 PDF：正文解析、证据定位、结构化抽取；
- arXiv：后续补充预印本与开放 PDF；
- GitHub Repository：仅用于生成 Reproduction Checklist，不执行任意代码。

建议选择论文类型：

- 基础或经典 Self-Interpretable GNN；
- 代表性方法论文；
- 针对不同任务、数据集或解释方法的论文；
- 明确讨论 limitation / future work 的论文；
- 可能构成相似或冲突路线的论文；
- 有公开代码仓库的部分论文。

---

## 4. 标注与真值设计

### 4.1 不要试图标注“唯一正确的 Research Gap”

Research Opportunity 本身具有开放性，不存在一个绝对唯一答案。

因此 Benchmark 不应问：

> 系统是否找到了唯一正确的 Gap？

而应问：

> 系统生成的 Opportunity 是否有充分证据、是否遗漏重要相似工作、是否具备新颖性和可行性，以及专家是否愿意进一步探索？

因此需要将“客观事实评测”和“开放式专家判断”分开。

---

### 4.2 第一层：论文级结构化事实标注

每篇论文标注以下内容。

| 对象            | 标注内容                           |
| --------------- | ---------------------------------- |
| Paper           | 标题、年份、会议/期刊、领域、摘要  |
| Research Task   | 论文解决的任务与问题设置           |
| Method          | 方法名称、主要思想、关键组件       |
| Dataset         | 使用的数据集与任务划分             |
| Baseline        | 对比方法                           |
| Metric          | 指标及其含义                       |
| Result          | 关键实验结果与适用范围             |
| Claim           | 作者的主要结论                     |
| Limitation      | 论文明确或隐含的局限               |
| Future Work     | 作者提出的后续方向                 |
| Evidence Span   | 支持上述事实的原文段落、页码、章节 |
| Code Repository | 若存在，记录仓库地址               |
| Confidence      | 标注者对该标注的置信度             |

必须记录 `Evidence Span`，即每个结构化事实回链到论文中的具体位置。

例如：

```text
Method:
  Name: Self-Interpretable GNN Method X
  Evidence:
    - Section 3.1, paragraph 2
    - Page 4, lines ...
```

这会成为 GapMind 的核心可信度能力：所有重要输出都可点击回到论文原文。

------

### 4.3 第二层：论文间关系标注

需要人工标注一部分高价值关系，而不是一开始标完整知识图谱。

推荐关系：

```
Paper --proposes--> Method
Method --extends--> Method
Method --compares_with--> Method
Paper --evaluates_on--> Dataset
Paper --reports--> Result
Paper --claims--> Claim
Paper --mentions_limitation--> Limitation
Paper --suggests--> Future Work
Paper --related_to--> Paper
Paper --contradicts_or_qualifies--> Claim
Method --suitable_for--> Task
Method --limited_on--> Task / Dataset / Condition
```

特别重要的是以下三类：

1. `extends`：方法 A 是不是方法 B 的扩展或改进；
2. `compares_with`：论文是否在相同任务或数据集上比较；
3. `contradicts_or_qualifies`：论文是否限制、反驳或改变了另一项主张的适用范围。

这些关系是系统避免把“Future Work”误认为真正 Research Opportunity 的基础。

------

### 4.4 第三层：Opportunity Case 标注

为 Evaluation Set 手工设计 5–10 个 Opportunity Case。

每个 Case 不是一个答案，而是一个“研究判断场景”。

推荐格式：

```
Case ID
Research Topic
Input Paper Set
Expected Relevant Papers
Expected Similar / Conflicting Works
Known Evidence
Potential Opportunity Directions
Known Risks
Invalid / Weak Opportunity Examples
Expert Evaluation Criteria
```

示例结构：

```
Topic:
  Self-interpretable GNN for node classification under distribution shift.

Known Evidence:
  - Paper A provides intrinsic explanation but only evaluates in-distribution.
  - Paper B studies distribution shift for GNNs but lacks interpretability.
  - Paper C applies explanation under a different task setting.
  - Paper D partially overlaps with the proposed combination.

Potential Opportunity:
  Investigate whether intrinsic interpretability remains faithful and useful
  under distribution shift, while comparing against Paper D.

Required Counter-Evidence:
  The system must surface Paper D rather than claiming the topic is unexplored.

Risk:
  “Combining interpretability and distribution shift” alone is not a sufficient
  novelty claim; task setting and methodological distinction must be explicit.
```

这类 Case 能准确检验系统是否真的识别“证据支持的机会”，而不是简单拼接关键词。

------

## 5. Opportunity 的质量标准

GapMind 输出的每个 Opportunity 必须满足以下结构：

```
Opportunity
├── Problem Statement
├── Research Scope
├── Supporting Evidence
├── Similar Work
├── Counter-Evidence / Conflicting Evidence
├── Why Existing Work Is Insufficient
├── Candidate Research Question
├── Candidate Hypothesis
├── Candidate Validation Plan
├── Novelty Score
├── Feasibility Score
├── Impact Score
├── Confidence
├── Open Risks
└── Human Decision
```

### 5.1 Opportunity 合格条件

一个候选 Opportunity 至少需要：

- 有至少 2–3 篇不同论文提供证据；
- 至少有一个具体论文原文片段作为关键判断依据；
- 检索并展示最相似的已有工作；
- 明确指出相似工作与候选方案的差异；
- 不能仅以 Future Work 或 Limitation 作为唯一证据；
- 包含潜在反证、风险或不确定性；
- 有可执行的验证方向；
- 输出为 `Candidate / Proposal`，而不是自动写成事实；
- 只有经过用户确认后才能成为 Workspace 中的 Confirmed Opportunity。

### 5.2 Opportunity 评审量表

每项以 1–5 分评分。

| 维度                       | 问题                                                    |
| -------------------------- | ------------------------------------------------------- |
| Evidence Grounding         | 关键判断是否有充分且准确的文献证据？                    |
| Citation Accuracy          | 引用是否真的支持相应的陈述？                            |
| Similar Work Coverage      | 是否识别了最相关的已有工作？                            |
| Counter-Evidence Awareness | 是否展示了冲突、重叠或削弱其新颖性的证据？              |
| Novelty                    | 是否仍存在值得探索的差异化空间？                        |
| Feasibility                | 在目标用户的资源与技能约束下是否可验证？                |
| Research Significance      | 是否具有学术或实际价值？                                |
| Actionability              | 是否能转化为 Research Question、Hypothesis 和实验计划？ |
| Explainability             | 研究者能否理解系统为何提出该 Opportunity？              |

建议让至少两位具有 AI/CS 研究经验的评审者独立评分；若条件有限，至少由团队中两位成员独立标注，再讨论分歧。

------

## 6. Baseline 设计

Benchmark 的目的不是证明 GapMind 比“什么都没有”强，而是拆解每层架构的价值。

### Baseline 0：Keyword Search + Manual Reading

流程：

```
Topic Keywords
→ Semantic Scholar Search
→ Top-k Papers
→ 人工阅读摘要或用户自行判断
```

作用：

- 代表最基本的文献检索工作流；
- 说明 GapMind 的价值不只是搜索。

### Baseline 1：Vanilla RAG

流程：

```
Topic / User Question
→ PDF Chunk Retrieval
→ LLM Summary / Gap Generation
```

限制：

- 不要求结构化实体；
- 不建立跨论文关系；
- 不强制展示相似工作或反证。

作用：

- 代表常见“上传论文 + 问答”产品；
- 是最重要的比较对象。

### Baseline 2：Structured Extraction without Graph Reasoning

流程：

```
Papers
→ LLM Structured Extraction
→ Structured Facts
→ LLM Opportunity Generation
```

作用：

- 验证“只做结构化抽取”是否已足够；
- 用来比较 Knowledge Graph 的额外价值。

### GapMind MVP：Structured Knowledge Graph + Evidence-aware Discovery

流程：

```
Semantic Scholar / PDF
→ Structured Fact Extraction
→ Evidence Linking
→ Knowledge Graph Construction
→ Similar-work / Counter-evidence Retrieval
→ Opportunity Generation
→ Human Confirmation
```

核心差异：

- Opportunity 必须带证据；
- 必须展示相似工作；
- 必须处理反证或重叠；
- 必须产生可审核 Proposal；
- 必须记录用户确认、修改或拒绝。

### Future Baseline：Graph Ranking / GNN Ranking

流程：

```
Knowledge Graph
→ Graph Feature Construction
→ Rule-based / Learning-to-Rank / GNN Ranker
→ Opportunity Candidate Ranking
```

注意：这不是 MVP 的必要比较项。

------

## 7. 评价指标

## 7.1 信息抽取评价

对标注过的论文级事实进行评价。

| 任务                    | 指标                                      |
| ----------------------- | ----------------------------------------- |
| Entity Extraction       | Precision / Recall / F1                   |
| Relation Extraction     | Precision / Recall / F1                   |
| Evidence Span Grounding | Evidence Precision / Recall，或人工正确率 |
| Citation Linking        | 引文定位正确率                            |
| Canonicalization        | 方法、数据集、指标名称归一化准确率        |

MVP 不必追求全自动严格 span-level F1。可以先采用：

- 自动匹配实体与关系；
- 对关键字段人工抽检；
- 对 Evidence Span 进行“是否支持该结论”的人工判定。

## 7.2 检索评价

对于每个 Opportunity Case，人工指定关键参考论文和相似/反证论文。

| 指标                    | 含义                         |
| ----------------------- | ---------------------------- |
| Recall@k                | Top-k 是否召回关键论文       |
| MRR                     | 最关键论文是否排在前面       |
| nDCG@k                  | 相关论文是否具有较好排序     |
| Similar Work Recall     | 是否召回最相近的已有工作     |
| Counter-Evidence Recall | 是否召回关键反证或限制性工作 |

对于 GapMind，`Counter-Evidence Recall` 是非常重要的差异化指标。

## 7.3 Opportunity 评价

由人工专家使用 1–5 量表评价：

- Evidence Grounding；
- Similar Work Coverage；
- Counter-Evidence Awareness；
- Novelty；
- Feasibility；
- Actionability；
- Explainability。

也可以计算：

```
Accept Rate
= 被专家接受或“编辑后接受”的 Opportunity 数
  / 总 Opportunity 数
Unsupported Opportunity Rate
= 缺少充分证据或存在明显错误的 Opportunity 数
  / 总 Opportunity 数
Missed Similar Work Rate
= Opportunity 未提及关键重叠论文的次数
  / Opportunity 总数
```

## 7.4 Human-in-the-Loop 评价

记录用户的实际交互，而不是只展示最终输出。

| 指标                     | 含义                                         |
| ------------------------ | -------------------------------------------- |
| Acceptance Rate          | 用户直接接受 Opportunity 的比例              |
| Edit-before-Accept Rate  | 用户修改后接受的比例                         |
| Rejection Rate           | 用户拒绝的比例                               |
| Defer Rate               | 用户标记为待调查的比例                       |
| Evidence Inspection Rate | 用户查看证据原文的比例                       |
| Time to Decision         | 从 Agent 输出到用户决策的时间                |
| Plan Conversion Rate     | 已确认 Opportunity 转为 Research Plan 的比例 |
| User Trust Score         | 用户对结果可信度的主观评分                   |
| Perceived Usefulness     | 用户对效率与价值的主观评分                   |

注意：高接受率不一定说明系统优秀。若系统只输出非常保守、泛泛的建议，接受率可能高但价值低。因此必须与 Novelty、Actionability、Counter-Evidence Awareness 一起解释。

## 7.5 系统工程指标

比赛中建议展示：

- 每篇 PDF 的解析耗时；
- 每篇论文的结构化抽取耗时；
- 索引耗时；
- Discover 任务总耗时；
- Agent 的模型调用次数；
- Token 使用量与成本；
- 失败率；
- 完整 Opportunity 的证据覆盖率。

这会让系统显得可工程化，而不只是一次性的 Prompt Demo。

------

## 8. 推荐实验协议

### Experiment A：结构化抽取可靠性

输入：

- 20–30 篇人工标注论文。

比较：

- 基础 LLM 抽取；
- Prompt 优化后的 GapMind Extractor；
- 可选：不同 LLM 模型。

输出：

- Entity / Relation / Evidence Grounding 指标；
- 错误类型分析。

重点错误类型：

- 把 Future Work 当作当前论文结论；
- 把 Baseline 当成论文贡献；
- 混淆数据集与任务；
- 将作者推测写成确定事实；
- 证据段落不支持提取结论；
- 方法同名但语义不同；
- 方法别名未归一。

### Experiment B：检索与相似工作覆盖

输入：

- 5–10 个 Self-Interpretable GNN Opportunity Case。

比较：

- Keyword Search；
- Vanilla RAG；
- Structured Retrieval；
- Graph-aware Retrieval。

输出：

- Recall@k；
- Similar Work Recall；
- Counter-Evidence Recall；
- 人工评估的检索相关性。

### Experiment C：Opportunity Discovery 质量

输入：

- 每个 Case 的主题、论文集合和用户约束。

比较：

- Vanilla RAG 直接生成 Gap；
- Structured Facts 生成 Opportunity；
- GapMind Evidence-aware Graph Discovery。

输出：

- 专家量表评分；
- Accept / Edit / Reject 分布；
- Unsupported Opportunity Rate；
- Missed Similar Work Rate；
- 典型正例与失败案例。

### Experiment D：Human-in-the-Loop 的价值

流程：

```
AI 输出 Opportunity Proposal
→ 研究者查看证据、相似工作与风险
→ 接受 / 编辑 / 拒绝 / 延后
→ 系统生成 Research Plan Proposal
```

对比：

- 无证据的普通 LLM 输出；
- 有证据但无确认机制的输出；
- GapMind 的 Evidence + Confirmation 输出。

可评估：

- 用户信任；
- 决策时间；
- 用户修改率；
- 最终 Plan 的可行性评分；
- 用户是否认为系统帮助发现了原本忽略的相关工作。

------

## 9. Demo Benchmark Case 建议

比赛 Demo 建议只选择一个控制良好的主题，而不是展示多个不完整领域。

推荐主题：

> Self-Interpretable GNNs：解释性、稳健性、泛化性与分布偏移之间的研究机会。

可以准备 8–12 篇高质量论文，包括：

- Self-Interpretable GNN 的代表方法；
- Post-hoc Explainability 方法；
- GNN Robustness / Distribution Shift 工作；
- 对解释性可信度、保真度或稳定性的批判性工作；
- 有清晰 baseline、数据集和开源仓库的工作。

Demo 要刻意包含至少一篇“看似支持候选 Gap、但实际上已部分覆盖该问题”的论文。

这能展示 GapMind 的核心能力：

```
不是只说：
“论文 A 的 Future Work 说可以研究方向 X。”

而是能够说：
“方向 X 具有研究价值，但 Paper D 已在 Setting S 中覆盖了部分问题；
真正值得验证的差异在于 Setting T、Metric M 或 Method Constraint C。
以下是支持与反证证据，以及可执行的验证计划。”
```

------

## 10. Benchmark 数据资产与版本管理

每次评测必须记录：

```
Corpus Version
Paper List
PDF Source
Semantic Scholar Metadata Snapshot
Annotation Version
Prompt Version
Model Provider and Model Version
Embedding Model Version
Retrieval Configuration
Graph Construction Rule Version
Opportunity Ranking Rule Version
Evaluation Script Version
Human Raters
Evaluation Date
```

原因：

- Semantic Scholar 返回结果会变化；
- LLM 输出具有随机性；
- Prompt 和模型变化会影响结果；
- 知识图谱会持续增量；
- 后续论文需要可复现实验。

------

## 11. MVP 的最低完成标准

MVP 不要求完成所有自动评测，但至少应完成：

- 30–50 篇 Self-Interpretable GNN 相关论文的可检索语料；
- 至少 10 篇论文的人工结构化事实与证据标注；
- 至少 5 个 Opportunity Case；
- 结构化抽取结果可视化；
- 基础知识图谱可视化；
- 至少一种 Similar Work / Counter-Evidence 展示；
- Discover Agent 输出 Evidence-grounded Opportunity；
- 用户可以接受、编辑、拒绝或延后 Opportunity；
- Timeline 能记录 AI 提议和用户决策；
- 一个从 Opportunity 到 Research Plan 的完整 Demo；
- 至少与 Vanilla RAG 进行定性或小规模定量对比。

------

## 12. 不应作为 MVP Benchmark 目标的内容

以下内容应明确延后：

- 在开放领域证明系统能自动发现真正“从未被提出”的研究课题；
- 自动评估所有 Research Gap 的绝对正确性；
- 自动运行任意 GitHub Repository；
- 证明 GNN 已优于所有 LLM 或规则方法；
- 构建覆盖全部 AI/CS 文献的大规模知识图谱；
- 大规模多用户研究；
- 复杂团队协作和权限评测；
- 论文投稿或 Rebuttal 的自动化质量证明。

MVP 的目标是证明：

> GapMind 能够在一个受控、明确的 AI/CS 研究子领域中，让研究机会发现过程更有证据、更能发现相似工作和反证、更容易被研究者审查与转化为可验证计划。