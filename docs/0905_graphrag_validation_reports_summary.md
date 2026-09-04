# GraphRAG JSON 验证报告汇总

更新时间：2026-09-05
基线 commit：`7a12844 feat: 完善知识图谱与论文工作台能力`

## 1. 汇总范围

本文件汇总 `evaluation/graphrag/reports/` 目录下截至 2026-09-05 的全部 14 份 JSON 报告：

- 9 份 `dense_vs_graph_shadow_*.json`：dense-only 与 GraphRAG shadow 对照评测。
- 5 份 `historical_chunk_*.json`：历史论文分块审计、修复及修复后审计。

本轮对照评测使用：

- Gold Set：[`demo_sig_ood_v1.json`](../evaluation/retrieval/gold/demo_sig_ood_v1.json)
- 查询数：6；`top_k=6`
- Workspace：`123100ea-e75b-4110-9048-1f5b92668c32`
- 图投影：PostgreSQL bounded SQL
- 图数据库：无（未引入 Neo4j）
- 回答上下文：`dense_only`
- LLM 调用：否；数据库写入：否

因此，以下 GraphRAG 指标是 shadow/diagnostic 观测，不代表 GraphRAG 已改变正式回答，也不单独构成回答质量上线门槛。

## 2. 报告清单

| 报告 | 类型 | 关键结果 | 状态 |
|---|---|---|---|
| [`historical_chunk_audit_pre_repair.json`](../evaluation/graphrag/reports/historical_chunk_audit_pre_repair.json) | 历史审计 | 76 篇中 50 篇正常、19 篇需修复、2 篇需人工复核 | 修复前基线 |
| [`historical_chunk_repair_20260904.json`](../evaluation/graphrag/reports/historical_chunk_repair_20260904.json) | 修复尝试 1 | 19/19 失败：重新解析文本与现有 parsed_text 不一致，触发保护校验 | 未完成 |
| [`historical_chunk_repair_20260904_v2.json`](../evaluation/graphrag/reports/historical_chunk_repair_20260904_v2.json) | 修复尝试 2 | 19/19 失败：Embedding API 连接失败 | 未完成 |
| [`historical_chunk_repair_20260904_v3.json`](../evaluation/graphrag/reports/historical_chunk_repair_20260904_v3.json) | 修复尝试 3 | 19/19 成功完成源文件保留式修复 | 完成 |
| [`historical_chunk_audit_final_20260904.json`](../evaluation/graphrag/reports/historical_chunk_audit_final_20260904.json) | 修复后审计 | 69 篇正常、0 篇需修复、2 篇仍需人工复核、5 篇未解析 | 最终状态 |
| [`dense_vs_graph_shadow_20260904.json`](../evaluation/graphrag/reports/dense_vs_graph_shadow_20260904.json) | Shadow 初始基线 | 查询命中率 100%；dense evidence hit rate 为 0；GraphRAG 路径证据率字段尚未展开；6/6 截断 | 诊断基线 |
| [`dense_vs_graph_shadow_20260904_v2.json`](../evaluation/graphrag/reports/dense_vs_graph_shadow_20260904_v2.json) | Shadow v2 | dense evidence 仍为 0；GraphRAG 路径证据率 77.12%；6/6 截断 | 诊断 |
| [`dense_vs_graph_shadow_20260904_v3.json`](../evaluation/graphrag/reports/dense_vs_graph_shadow_20260904_v3.json) | Shadow v3 | dense evidence 修复为 100%；GraphRAG 路径证据率仍为 77.12%；6/6 截断 | 诊断 |
| [`dense_vs_graph_shadow_20260904_v4.json`](../evaluation/graphrag/reports/dense_vs_graph_shadow_20260904_v4.json) | Shadow v4 | 46 条路径、35 条含证据；宏平均 77.12%、微平均 76.09%；6/6 截断 | 诊断 |
| [`dense_vs_graph_shadow_20260904_v5.json`](../evaluation/graphrag/reports/dense_vs_graph_shadow_20260904_v5.json) | Shadow v5 | 41/41 路径含证据；路径证据率 100%；4/6 截断 | 诊断 |
| [`dense_vs_graph_shadow_20260904_v6.json`](../evaluation/graphrag/reports/dense_vs_graph_shadow_20260904_v6.json) | Shadow v6 | 41/41 路径含证据；路径证据率 100%；4/6 截断 | 诊断 |
| [`dense_vs_graph_shadow_20260904_v7.json`](../evaluation/graphrag/reports/dense_vs_graph_shadow_20260904_v7.json) | Shadow v7 | 41/41 路径含证据；路径证据率 100%；4/6 截断 | 优化前最终基线 |
| [`dense_vs_graph_shadow_20260905_v8.json`](../evaluation/graphrag/reports/dense_vs_graph_shadow_20260905_v8.json) | Shadow v8 | 73 条候选路径中输出 41 条，32 条因 `node_limit` 丢弃；路径证据率 100%；4/6 截断 | 候选预算优化 |
| [`dense_vs_graph_shadow_20260905_v9.json`](../evaluation/graphrag/reports/dense_vs_graph_shadow_20260905_v9.json) | Shadow v9 | 73 条候选路径中输出 71 条，仅 2 条因 `node_limit` 丢弃；路径证据率 100%；1/6 截断 | 当前最佳观测 |

## 3. 历史分块修复结果

### 3.1 审计状态变化

| 阶段 | 论文总数 | 正常 | 未解析跳过 | 需修复 | 需人工复核 | Milvus 不可用 |
|---|---:|---:|---:|---:|---:|---:|
| 修复前审计 | 76 | 50 | 5 | 19 | 2 | 0 |
| 修复尝试 1 后 | 76 | 50 | 5 | 19 | 2 | 0 |
| 修复尝试 2 后 | 76 | 50 | 5 | 19 | 2 | 0 |
| 修复尝试 3 后 | 76 | 69 | 5 | 0 | 2 | 0 |
| 最终审计 | 76 | 69 | 5 | 0 | 2 | 0 |

19 篇异常分块最终修复完成。两次失败尝试保留在报告中，分别对应：

1. 重新解析保护校验拒绝覆盖现有 parsed_text；
2. Embedding 服务连接失败；
3. 第三次使用可用的源文件保留式流程完成修复，并由最终审计确认 `needs_repair=0`。

仍需人工复核的两篇论文属于同一 Workspace `f98b15b4-0326-433e-96a3-bddfd0b77402`：

- `A-MEM: Agentic Memory for LLM Agents`
- `01Survey`

两篇均缺少 `parsed_text` 文件和 `chunk_index` 文件，观察到的 chunk 数为 0；它们不是本轮自动修复候选，不能将其标记为自动修复成功。

## 4. Dense-only 与 GraphRAG shadow 演进

### 4.1 聚合指标

延迟单位为毫秒；`dense+shadow` 是顺序执行 dense 检索和 shadow 诊断的估算值，不是正式回答延迟。

| 报告 | Dense 查询命中 | Dense evidence | GraphRAG 查询证据 | 路径 / 含证据 | 路径证据率（宏） | 截断查询数 | Dense 均值 | Shadow 均值 | 估算合计 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 20260904 | 100% | 0% | 100% | 未展开 | — | 6/6 | 930.54 | 68.68 | 999.22 |
| v2 | 100% | 0% | 100% | 未展开 | 77.12% | 6/6 | 842.01 | 68.63 | 910.64 |
| v3 | 100% | 100% | 100% | 未展开 | 77.12% | 6/6 | 1402.08 | 64.10 | 1466.18 |
| v4 | 100% | 100% | 100% | 46 / 35 | 77.12% | 6/6 | 909.61 | 101.07 | 1010.68 |
| v5 | 100% | 100% | 100% | 41 / 41 | 100% | 4/6 | 895.89 | 100.95 | 996.83 |
| v6 | 100% | 100% | 100% | 41 / 41 | 100% | 4/6 | 937.77 | 95.53 | 1033.30 |
| v7 | 100% | 100% | 100% | 41 / 41 | 100% | 4/6 | 833.52 | 92.44 | 925.96 |
| v8 | 100% | 100% | 100% | 41 / 41 | 100% | 4/6 | 876.15 | 89.05 | 965.20 |
| v9 | 100% | 100% | 100% | 71 / 71 | 100% | 1/6 | 847.16 | 90.56 | 937.73 |

### 4.2 v8 到 v9 的必要优化效果

v9 对图路径进行拓扑压缩：节点集合只保留 `chunk`、`paper`、`canonical_entity`，而 `item_id`、`evidence_span_id`、EvidenceSpan 摘要和定位信息保留在路径/边的 provenance 中。结果如下：

| 指标 | v8 | v9 | 变化 |
|---|---:|---:|---:|
| 候选路径 | 73 | 73 | — |
| 输出路径 | 41 | 71 | +30 |
| 丢弃路径 | 32 | 2 | -30 |
| 丢弃原因 | `node_limit:32` | `node_limit:2` | 明显减少 |
| 路径含证据 | 41/41 | 71/71 | 保持 100% |
| 截断查询数 | 4/6 | 1/6 | 减少 3 个查询 |
| Shadow 均值 | 89.05 | 90.56 | +1.51 ms |

这表明 v9 主要改善了路径可展示性和 `node_limit` 截断，并未改变回答上下文；它仍然是 shadow 诊断结果。

## 5. v9 的逐查询结果

| ID | 类型 | 查询 | Dense ms | 候选→输出 | 含证据路径 | Shadow ms | 截断 |
|---|---|---|---:|---:|---:|---:|---|
| ss-001 | semantic_search | self-interpretable graph neural network prototype-based explanation | 1196.49 | 12→12 | 12/12 | 157.51 | 否 |
| ss-002 | semantic_search | systematic evaluation of explainability methods for graph neural networks | 846.65 | 6→6 | 6/6 | 81.66 | 否 |
| ss-003 | semantic_search | graph out-of-distribution generalization benchmark covariate concept shift | 768.69 | 6→6 | 6/6 | 47.57 | 否 |
| ce-001 | counter_evidence | Intrinsic explanation methods always produce more faithful explanations than post-hoc explanation methods. | 821.47 | 9→9 | 9/9 | 98.59 | 否 |
| ce-002 | counter_evidence | Explanations learned on synthetic motifs transfer reliably to real-world graph datasets. | 680.29 | 27→25 | 25/25 | 74.55 | 是，`node_limit` |
| ce-003 | counter_evidence | Self-interpretable GNN explanations remain stable under distribution shift without special out-of-distribution training. | 769.39 | 13→13 | 13/13 | 83.50 | 否 |

v9 汇总：

- Dense query hit rate：`1.0`。
- Dense evidence hit rate：`1.0`。
- GraphRAG query evidence hit rate：`1.0`。
- GraphRAG 路径证据率：宏平均 `1.0`，微平均 `1.0`。
- GraphRAG fallback：`0` 次。
- 非法路径边：`0`；Workspace 越界：`0`。
- Dense 均值 / 最大值：`847.16 / 1196.49 ms`。
- Shadow 均值 / 最大值：`90.56 / 157.51 ms`。
- 顺序执行估算均值：`937.73 ms`。

## 6. 指标和数据语义解释

报告中的 evidence hit rate 是可回链率，不是语义相关性评分：

- Dense：返回 chunk 中，能依据完整文本或 Milvus 8,000 字符限制下保存的 canonical prefix 进行证据回链的比例。
- GraphRAG：输出路径中，能通过 EvidenceSpan 重新检索并完成证据回链的路径比例。

因此，v9 的 `71/71` 表示 71 条路径都有可回链 EvidenceSpan，不表示 71 条证据都与用户问题高度相关，也不表示已经通过人工审阅。EvidenceSpan 的文本、字符范围、artifact 身份校验仍是最终定位边界；校验失败时不应展示黄色高亮，也不应把该路径加入回答上下文。

当前隔离和回退语义为：

- PostgreSQL 是业务真源和权限边界；Milvus 只提供 dense seed / 向量召回。
- 图投影查询必须绑定 `workspace_id` 并过滤软删除数据。
- `claim`、`limitation` 保持论文上下文，不按名称、文本或 embedding 自动合并。
- 每条路径边的 `source`、`target` 必须合法，且端点必须存在于本次返回节点中。
- GraphRAG 失败、超时、版本不一致或证据不足时，正式回答安全保留 dense 检索结果。
- 当前 shadow 评测没有调用 LLM，也没有写入业务数据库；不能将候选路径当成人工确认事实。

## 7. 结论、限制和后续条件

### 已验证

1. 历史分块异常中的 19 篇已完成自动修复，最终审计中 `needs_repair=0`。
2. v9 在当前 6 条 Gold 查询上保持 dense 召回和证据可回链率 100%。
3. GraphRAG 路径的 EvidenceSpan 回链、路径端点完整性和 Workspace 隔离检查通过。
4. 拓扑压缩将路径截断从 4/6 降至 1/6，并把输出路径从 41 条提升到 71 条。

### 尚不能由这些 JSON 证明

1. GraphRAG 比 dense-only 更能回答复杂问题；当前评测仍是 6 条查询，且正式回答上下文为 dense-only。
2. 路径证据的人工相关性、充分性和可读性；当前报告没有人工 `human_verdict`。
3. `ce-002` 的 2 条路径已完全展示；该查询仍发生 `node_limit` 截断。
4. 生产环境真实高并发下的 SQL 投影性能；当前延迟是一次观测，不能直接作为扩容或换库依据。

### Neo4j 状态

本阶段未引入 Neo4j。只有在真实数据、固定 Gold 和可重复压测证明 PostgreSQL bounded SQL 在性能或关系表达能力上存在明确瓶颈后，才进入 Neo4j 可选只读投影评估；届时 PostgreSQL 仍保持业务真源，Neo4j 只能是可重建的只读投影。

## 8. 复现入口

v9 对照报告可使用以下命令重新生成（需要本地评测依赖和可用的 Milvus/Embedding 服务；命令不会改变回答上下文）：

```powershell
cd D:\MyCode\Spark-competition\refactor\GapMind
backend\.venv\Scripts\python.exe evaluation\graphrag\run_live_comparison.py `
  --workspace-id 123100ea-e75b-4110-9048-1f5b92668c32 `
  --gold evaluation\retrieval\gold\demo_sig_ood_v1.json `
  --top-k 6 `
  --output evaluation\graphrag\reports\dense_vs_graph_shadow_20260905_v9.json
```

本文件是对现有 JSON 产物的只读总结，不替代 JSON 原始明细，也没有执行 commit 或 push。
