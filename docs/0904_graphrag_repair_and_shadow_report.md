# 历史分块修复与 GraphRAG Shadow 评测记录

日期：2026-09-04—2026-09-05

基线：`7a12844 feat: 完善知识图谱与论文工作台能力`

## 1. 历史分块复核与修复

使用只读审计脚本先检查 PostgreSQL 中未软删除的 Paper、当前 `parsed_text` Artifact、当前 `chunk_index` Artifact、分块 JSONL 的 Contract B 字段、字符范围和 Milvus 工作区内 chunk 身份。修复时不重新解析 PDF，不修改 `parsed_text`、EvidenceSpan 或知识抽取结果，而是基于现有不可变 `parsed_text` 创建新的合法 `chunk_index` Artifact，并强制重建该论文在 Milvus 中的向量。旧 Artifact 保留，未做硬删除。

| 阶段 | 论文总数 | 已通过 | 未解析跳过 | 可自动修复 | 需人工处理 | Milvus 不可用 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 修复前 | 76 | 50 | 5 | 19 | 2 | 0 |
| 修复后最终复核 | 76 | 69 | 5 | 0 | 2 | 0 |

19 篇历史异常已完成分块重建和 Milvus 重建。剩余 2 篇为同一工作区中的 `A-MEM: Agentic Memory for LLM Agents` 与 `01Survey`，数据库仍有 Artifact 记录，但对应的 `parsed_text`/`chunk_index` 文件已经不在本地存储中，缺少可安全重建的源文本，因此保留为 `needs_manual_review`，没有猜测或覆盖数据。

复核报告：

- `evaluation/graphrag/reports/historical_chunk_repair_20260904_v3.json`
- `evaluation/graphrag/reports/historical_chunk_audit_final_20260904.json`

## 2. Dense-only 与 GraphRAG shadow 对照

评测脚本为只读运行，不调用 LLM、不写入 Chat、不改变 Gold Set。6 条查询来自现有 `demo_sig_ood_v1` Gold Set：3 条 semantic search、3 条 counter evidence。

复现命令（从仓库根目录运行）：

```powershell
backend\.venv\Scripts\python.exe evaluation\graphrag\run_live_comparison.py `
  --workspace-id 123100ea-e75b-4110-9048-1f5b92668c32 `
  --gold evaluation\retrieval\gold\demo_sig_ood_v1.json `
  --top-k 6 `
  --output evaluation\graphrag\reports\dense_vs_graph_shadow_20260904_v7.json
```

- dense-only：现有 Milvus dense seed + reranker，`top_k=6`；它是唯一回答上下文。
- GraphRAG shadow：复用同一批 dense seed，执行 PostgreSQL bounded SQL projection（`max_hops=2`、`node_limit=32`、`edge_limit=64`），再对图路径关联的 EvidenceSpan 做证据回检；只记录诊断，不注入回答。
- dense 证据命中率：可通过 `workspace_id`、`paper_id`、`chunk_id`、source Artifact 和规范文本（全文或 Milvus 保存的前 8,000 字符）回链的返回 chunk 数 / 返回 chunk 数。
- GraphRAG 路径证据命中率：按查询宏平均为各查询“含有回检 EvidenceSpan 的合法路径数 / 该查询路径总数”的平均值；本轮全局微平均为 35/46 = 76.09%。

### 汇总结果

| 指标 | Dense-only | GraphRAG shadow |
| --- | ---: | ---: |
| 查询命中率 / 查询证据命中率 | 100% | 100% |
| 证据命中率 | 100% | 77.12%（按查询宏平均）；76.09%（全局 35/46 条路径） |
| 平均延迟 | 909.61 ms | 101.07 ms（额外 shadow 开销） |
| 最大延迟 | 1,499.19 ms | 280.34 ms |
| fallback 次数 | — | 0 |
| `node_limit` 截断次数 | — | 6/6 |
| dense + shadow 估计平均延迟 | — | 1,010.68 ms |

| Query ID | 类型 | Dense 延迟 | 路径数 | 含证据路径 | 路径证据命中率 | Shadow 延迟 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `ss-001` | semantic | 1,499.19 ms | 8 | 8 | 100.00% | 280.34 ms |
| `ss-002` | semantic | 874.20 ms | 7 | 5 | 71.43% | 110.69 ms |
| `ss-003` | semantic | 711.78 ms | 9 | 5 | 55.56% | 47.20 ms |
| `ce-001` | counter | 772.28 ms | 8 | 4 | 50.00% | 60.12 ms |
| `ce-002` | counter | 813.68 ms | 7 | 7 | 100.00% | 53.40 ms |
| `ce-003` | counter | 786.55 ms | 7 | 6 | 85.71% | 54.64 ms |

原始 JSON 报告：`evaluation/graphrag/reports/dense_vs_graph_shadow_20260904_v4.json`。

注意：本轮只证明投影、路径完整性和证据身份回链的运行观测，不把机械命中率当作回答质量 Gate；尚未执行人工 `human_verdict` 评审，也没有用该 shadow 结果替换默认回答。

## 3. Shadow 质量优化后的复测

针对 GIP 实际问题，优化前曾出现 8 条路径、其中 4 条没有证据、31 个节点并触发 `node_limit`。优化后：

- 查询模式不再输出零证据路径；无相关 EvidenceSpan 的候选只保留在内部筛选阶段。
- 同一论文的多条 dense seed 仍保留在审计 seed 列表，但每条路径只挂载该论文最佳 seed。
- 同一实体下优先选择具有最高查询相关证据的 KnowledgeItem，而不是按 UUID 截取。
- 每个 KnowledgeItem 每条路径只展示最高相关的一条 EvidenceSpan；关系路径要求两个端点都有当前问题相关证据，并去除重复关系签名。
- SQL 预取使用 256–512 条的有界 look-ahead，只有确实丢弃候选时才标记 `query_limit`，不再把恰好填满上限误报为截断。

GIP 复测结果：5 条路径、5 条含证据、0 条零证据路径、22 个节点、0 次截断、0 条 relation 重复路径。

最终全量 shadow 报告：`evaluation/graphrag/reports/dense_vs_graph_shadow_20260904_v7.json`。

| 指标 | Dense-only | 优化后 GraphRAG shadow |
| --- | ---: | ---: |
| 查询命中率 / 查询证据命中率 | 100% | 100% |
| 证据命中率 | 100% | 100%（41/41 条路径） |
| 平均延迟 | 833.52 ms | 92.44 ms（额外 shadow 开销） |
| 最大延迟 | 1,125.69 ms | 187.86 ms |
| fallback 次数 | — | 0 |
| `node_limit` 截断次数 | — | 4/6 |
| dense + shadow 估计平均延迟 | — | 925.96 ms |

当前仍有 4/6 条查询触发真实 `node_limit`，但未产生零证据路径；这属于有界结果容量限制，尚未通过扩大默认预算解决。默认回答仍为 dense-only，shadow 结果不进入 LLM 上下文。

## 3.1 候选路径全局预算优化复测

本轮将路径生成拆为“候选收集 → 证据优先排序 → 节点/边预算贪心打包”。排序依次考虑路径证据查询相关性、dense seed 分数、证据/知识项置信度、路径大小和路径 ID；当候选无法放入剩余预算时继续尝试后续候选，不再因一个大候选提前终止。`path_count` 保持为最终发出的路径数，并新增 shadow 诊断字段：`candidate_path_count`、`emitted_path_count`、`dropped_path_count`、`dropped_path_reasons`。

上一轮只读报告：`evaluation/graphrag/reports/dense_vs_graph_shadow_20260905_v8.json`。

| 指标 | Dense-only | 优化后 GraphRAG shadow |
| --- | ---: | ---: |
| 查询命中率 / 查询证据命中率 | 100% | 100% |
| 证据命中率 | 100% | 100%（41/41 条路径） |
| 候选路径 / 发出路径 / 预算丢弃 | — | 73 / 41 / 32 |
| 丢弃原因 | — | `node_limit`: 32 |
| 平均延迟 | 876.15 ms | 89.05 ms（额外 shadow 开销） |
| 最大延迟 | 1,234.77 ms | 148.68 ms |
| fallback 次数 | — | 0 |
| `node_limit` 截断次数 | — | 4/6 |
| dense + shadow 估计平均延迟 | — | 965.20 ms |

该诊断说明当前 4 条查询的路径容量仍受节点预算约束，但被丢弃的候选没有降低已发出路径的证据命中率；它不构成“GraphRAG 已优于 dense”的结论，也不改变默认回答上下文。

## 3.2 Shadow 拓扑压缩复测

为减少重复 EvidenceSpan 和 KnowledgeItem 对拓扑预算的占用，shadow 路径的 `nodes`/`edges` 现在只表达 `chunk`、`paper` 和 `canonical_entity` 拓扑；`item_id`、`evidence_span_id`、EvidenceSpan 原文、字符范围和 review status 继续保留在路径及边的 provenance 字段中。该变化不影响证据定位，也不改变回答上下文。

最新只读报告：`evaluation/graphrag/reports/dense_vs_graph_shadow_20260905_v9.json`。

| 指标 | v8 | v9 |
| --- | ---: | ---: |
| 发出路径 | 41 | 71 |
| 含证据路径 | 41/41 | 71/71 |
| 候选路径 | 73 | 73 |
| 预算丢弃 | 32（`node_limit`） | 2（`node_limit`） |
| `node_limit` 截断查询数 | 4/6 | 1/6 |
| fallback 次数 | 0 | 0 |
| shadow 平均延迟 | 89.05 ms | 90.56 ms |

拓扑压缩显著减少了预算截断，但会增加可展示路径数量；目前仍保持所有发出路径的 EvidenceSpan 证据命中率为 100%。如果真实 Workspace 中路径数量造成 UI 负担，下一步再单独增加展示层路径上限，不改变投影和回答语义。

## 4. 隔离与 fallback 保证

- PostgreSQL 仍是业务真源，Milvus 只负责 dense seed 和向量检索；本阶段没有引入 Neo4j。
- Milvus 查询、历史审计、论文映射和图投影均带 `workspace_id`；论文和 Artifact 均过滤软删除状态。
- 图路径端点在返回前校验，路径边必须引用本次结果中的合法 source/target；本次评测 6 条查询的非法边和 workspace violation 均为 0。
- 图投影失败、证据回检为空、超时或数据版本/身份校验失败时，回答继续使用 dense-only；shadow 仅记录 fallback 原因。
- 两篇缺少本地源文件的历史论文没有自动补造 chunk 或向量，等待人工恢复源文件后再处理。

## 5. 回归测试

在不修改根目录 `.env` 的前提下，使用显式测试环境变量隔离真实 LLM 配置：

- `backend/tests/test_knowledge_api.py`：17 passed
- 后端全量（含配置测试）：538 passed
- 前端 Vitest：17 files / 63 tests passed
- `npm run typecheck`：passed
- `npm run build`：passed
- `npm run lint`：0 errors，15 条既有 warnings
- 新增/修改 Python 脚本：`py_compile` passed

直接继承根目录 `.env` 的首次全量运行出现 4 个配置期望失败；这些失败分别来自 parser、remote model 和 backup endpoint 的 `.env` 覆盖，随后已用显式测试变量重跑并全部通过，未修改或提交真实密钥。

## 6. Neo4j 状态

本阶段暂不引入 Neo4j。只有在真实工作区数据上对 PostgreSQL bounded SQL projection 做基准测量，确认存在明确且可复现的性能瓶颈或表达能力瓶颈，并能说明只读可重建投影的收益后，才进入 Neo4j 可选阶段评估。
