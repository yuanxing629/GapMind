# Workspace Chat QA 评测

这个目录评测的是**已保存的 Chat 回答如何使用证据**，而不是再次运行模型。它补充 `evaluation/retrieval/`：后者验证论文能否被召回；这里验证回答中的 `[En]`、`[Pn]`、`[Dn]`、`[Cn]` 是否与该消息保存的来源一致，以及人工复核后的“可回答 / 证据不足”判断是否符合冻结 Gold Set。

## 边界

- 不调用 LLM，不访问 Milvus，不创建 Task 或 AgentRun。
- Gold Set 仅保存人工确认的预期问题、必需论文和预期结论类型；不能从 LLM 回答反向修改 Gold。
- `human_verdict` 是人工复核字段，值为 `supported`、`insufficient_evidence` 或 `unsupported`；脚本不会从回答文本推断事实正确性。
- 题目声明 `workspace_with_confirmed_plan` 时，观测快照必须有计划来源，且回答必须用有效 `[Pn]` 标记该计划；计划标记不等同于论文 `[En]`。
- 报告先展示指标，不预设质量阈值。收集足够人工样本并复核后，才可将阈值接入 Chat 的受限引用质量门。

## 文件结构

```text
evaluation/chat/
├── gold_set.py      # Gold 与观测快照的 Pydantic schema
├── metrics.py       # 复用 Chat 的引用解析规则，纯离线评分
├── run_eval.py      # JSON Gold + JSON 观测 → JSON 报告
├── gold/            # 人工维护、版本冻结的 QA 问题集
└── reports/         # 本机评测产物，gitignore
```

## Gold Set：黄金集

每个 `questions[]` 都应由人工标注：

- `expected_verdict: supported` 必须列出 `required_paper_refs`，回答需要把这些论文作为真实 `[En]` 引用。
- `expected_verdict: insufficient_evidence` 不得预置论文引用，用于检验系统不会编造论文支持。
- 需要计划上下文的题目使用 `context.mode: workspace_with_confirmed_plan` 与 `research_plan_ref`，它们是非论文来源，不能替代 `required_paper_refs`。
- 新样本先标记 `annotation_status: draft`；双人复核后冻结为 `gold`，已有题目不回写。

## 观测快照与运行

从 Chat API 的 assistant message 复制最小字段：`content` → `answer_text`、`grounding_status`、`citations[]` → `evidence[]`（只保留 `rank`/论文标题）、`sources[]` 中的计划/报告/代码来源 → `sources[]`。若进行了人工事实核验，再填 `human_verdict`。

如果消息已经由 `0023_chat_retrieval_audit` 写入审计，也可以复制非敏感的 `retrieval_audit`：状态、召回数、返回 chunk 数、最终论文数、reranker 状态和延迟。匿名导出器会丢弃 `request_id`，旧消息的空审计保持为 `null`。这些字段只用于观察检索覆盖与延迟，不参与事实正确性、Gold 生成或自动阈值判断。

也可以使用只读导出器从本地 PostgreSQL 复制真实持久化消息。默认不写入本地 `message_id`，且始终将 `human_verdict` 留空：

```powershell
backend\.venv\Scripts\python.exe evaluation\chat\export_observations.py `
  --workspace-id <workspace-id> `
  --case-id <draft-case-id> `
  --select chat-gnn-03=<assistant-message-id> `
  --select chat-gnn-04=<assistant-message-id> `
  --output evaluation\chat\reports\candidate_observations_draft.json
```

导出器只接受当前 workspace 中已完成的 assistant 消息，不调用 LLM、Milvus、Task 或 Agent，也不会修改 workspace。导出后仍需人工决定题目的 `expected_verdict` 和观测的 `human_verdict`，不能直接将候选样本当成 Gold。

```bash
cd D:\MyCode\Spark-competition\refactor\GapMind
backend\.venv\Scripts\python.exe evaluation\chat\run_eval.py \
  --gold evaluation\chat\gold\gnn_explanations_draft_v1.json \
  --observations <本机观测快照.json> \
  --output evaluation\chat\reports\gnn_explanations_local.json
```

退出码 `0` 只表示所有题目均有观测、没有失效的来源标记，且必需论文都被有效引用；`2` 表示机械检查失败或输入无效。它不是“回答事实正确”的自动保证，人工 `human_verdict` 指标会单独列出。
