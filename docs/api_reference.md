# GapMind API 接口参考

> Base URL: `http://localhost:8000/api/v1`
> Swagger UI: `http://localhost:8000/docs`
> OpenAPI JSON: `http://localhost:8000/openapi.json`
> 最后更新：2026-07-19（Phase 1b 完成）

## 状态标记

- ✅ **已实现**：当前版本可用，有测试覆盖
- 🔒 **只读**：表已建，读 API 可用，写 API 待后续 Phase
- 🚧 **计划中**：已规划，未实现
- ❌ **不做**：明确不在 MVP 范围

---

## 目录

- [Health（健康检查）](#health健康检查)
- [Workspace](#workspace)
- [Paper](#paper)
- [Artifact](#artifact)
- [Task](#task)
- [Timeline](#timeline)
- [Knowledge](#knowledge)
- [Retrieval（计划）](#retrieval计划)
- [Discover Agent（计划）](#discover-agent计划)
- [Opportunity（计划）](#opportunity计划)
- [Research Plan（计划）](#research-plan计划)
- [外部集成](#外部集成)

---

## Health（健康检查）

### `GET /health` ✅

Liveness 检查。永远返回 200，只要进程活着。

**响应**：
```json
{
  "status": "ok",
  "env": "development"
}
```

### `GET /health/ready` ✅

Readiness 检查。探测配置的外部服务（API Key 是否配置等）。

**响应**：
```json
{
  "status": "ok",
  "checks": {
    "llm_key": "ok",
    "embedding_key": "ok"
  }
}
```

---

## Workspace

Workspace 是核心 scope 对象，所有其他资源都归属于某个 workspace。

### `POST /workspaces` ✅

创建 workspace。

**请求体**：
```json
{
  "name": "Self-Interpretable GNN",
  "description": "可选",
  "topic": "Self-Interpretable Graph Neural Networks",
  "keywords": ["GNN", "explainability"],
  "goals": "Find a research gap with multi-paper evidence support.",
  "constraints": "Compute budget: 1 A100.",
  "active_questions": ["Why do post-hoc explainers fail on GNNs?"]
}
```

**响应** `201 Created`：
```json
{
  "id": "04f801d6-7473-4a8b-915f-60448a10d748",
  "name": "Self-Interpretable GNN",
  "description": null,
  "topic": "Self-Interpretable Graph Neural Networks",
  "keywords": ["GNN", "explainability"],
  "goals": "Find a research gap with multi-paper evidence support.",
  "constraints": "Compute budget: 1 A100.",
  "active_questions": ["Why do post-hoc explainers fail on GNNs?"],
  "is_archived": false,
  "is_deleted": false,
  "created_at": "2026-07-19T10:00:00Z",
  "updated_at": "2026-07-19T10:00:00Z"
}
```

**校验**：
- `name` 必填，1-255 字符，strip 后非空
- `keywords` / `active_questions` 自动 strip + 过滤空字符串

### `GET /workspaces` ✅

列出 workspaces（分页，默认排除 archived 和 deleted）。

**查询参数**：
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `include_archived` | bool | false | 是否包含归档项 |
| `limit` | int | 50 | 1-200 |
| `offset` | int | 0 | |

**响应**：
```json
{
  "items": [WorkspaceRead, ...],
  "total": 2,
  "limit": 50,
  "offset": 0
}
```

### `GET /workspaces/{workspace_id}` ✅

获取单个 workspace。软删除的返回 404。

**错误**：
- `404 workspace_not_found`：ID 不存在或已删除
- `404`：UUID 格式无效（也当 404 处理，不暴露验证细节）

### `PATCH /workspaces/{workspace_id}` ✅

部分更新。只更新传入的字段（PATCH 语义）。

**请求体**（所有字段可选）：
```json
{
  "name": "New Name",
  "keywords": ["new", "keywords"]
}
```

**响应**：完整的 `WorkspaceRead`

### `POST /workspaces/{workspace_id}/archive` ✅

归档 workspace。从默认列表消失，但可通过 `include_archived=true` 查看。

### `POST /workspaces/{workspace_id}/unarchive` ✅

取消归档。

### `DELETE /workspaces/{workspace_id}` ✅

软删除。行保留供审计，从所有查询消失。

**响应** `200`：
```json
{
  "id": "04f801d6-...",
  "deleted": true
}
```

---

## Paper

论文管理。两种创建方式：上传 PDF 或纯元数据创建。

### `POST /workspaces/{workspace_id}/papers/upload` ✅

上传 PDF 创建论文。multipart/form-data。

**未提供 title/authors/year 时**，后端 best-effort 从 PDF 内嵌 metadata 自动提取填充。

**请求体**（multipart）：
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | file | 是 | .pdf 文件，最大 50MB |
| `title` | string | 否 | 不填则用 PDF metadata 或 filename |
| `authors` | string | 否 | 逗号分隔，不填则用 PDF metadata |
| `year` | int | 否 | 不填则用 PDF metadata |
| `abstract` | string | 否 | |
| `doi` | string | 否 | |
| `arxiv_id` | string | 否 | |

**响应** `201 Created`：`PaperRead`

**错误**：
- `400 invalid_file`：非 .pdf 文件
- `400 empty_file`：空文件
- `413 file_too_large`：超过 50MB
- `422 validation_error`：字段校验失败
- `404 workspace_not_found`

### `POST /workspaces/{workspace_id}/papers` ✅

纯元数据创建论文（无 PDF）。后续可通过 `upload-pdf` 端点补传。

**请求体**：
```json
{
  "title": "Paper Title",
  "authors": ["Alice", "Bob"],
  "year": 2024,
  "abstract": "...",
  "doi": "10.xxx",
  "arxiv_id": "2401.12345"
}
```

**响应** `201 Created`：`PaperRead`（`primary_artifact_id` 为 null）

**校验**：
- `title` 必填（纯元数据创建必须有标题）

### `POST /workspaces/{workspace_id}/papers/{paper_id}/upload-pdf` ✅

给已有论文补传 PDF。用于"先加元数据后补 PDF"流程。

**请求体**（multipart）：同 `upload`，只需 `file`。

**行为**：
- 设置 `primary_artifact_id`
- 填充 paper 行上仍为空的字段（不覆盖已有值）
- 写 `paper.pdf_attached` timeline 事件

**错误**：
- `404 paper_not_found`
- `409 paper_already_has_pdf`：已有 PDF，不能重复添加
- `400 invalid_file` / `400 empty_file` / `413 file_too_large`

### `GET /workspaces/{workspace_id}/papers` ✅

列出 workspace 内的论文（分页）。

**查询参数**：`limit`（1-200，默认 50）、`offset`（默认 0）

**响应**：
```json
{
  "items": [PaperRead, ...],
  "total": 7,
  "limit": 50,
  "offset": 0
}
```

### `GET /workspaces/{workspace_id}/papers/{paper_id}` ✅

获取单个论文。跨 workspace 访问返回 404。

### `PATCH /workspaces/{workspace_id}/papers/{paper_id}` ✅

更新论文元数据。PATCH 语义。

**请求体**：所有元数据字段可选。

### `DELETE /workspaces/{workspace_id}/papers/{paper_id}` ✅

软删除论文。从列表消失，行保留。

**响应** `200`：`{"id": "...", "deleted": true}`

---

## Artifact

通用文件管理。Phase 1b 只读（创建通过 paper upload 间接完成）。

### `GET /workspaces/{workspace_id}/artifacts` ✅

列出 workspace 内的 artifact。

**查询参数**：
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `kind` | string | - | 过滤类型：`pdf` / `parsed_text` / `chunk_index` / `report` |

**响应**：`[ArtifactRead, ...]`

### `GET /workspaces/{workspace_id}/artifacts/{artifact_id}` ✅

获取单个 artifact。跨 workspace 访问返回 404。

**响应**：
```json
{
  "id": "...",
  "workspace_id": "...",
  "kind": "pdf",
  "file_path": "workspaces/04/.../artifacts/xxx.pdf",
  "original_filename": "paper.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 1234567,
  "is_deleted": false,
  "created_at": "...",
  "updated_at": "..."
}
```

### `GET /workspaces/{workspace_id}/artifacts/{artifact_id}/download` 🚧

下载 artifact 文件内容。Phase 2 实现（PDF 解析时需要读文件）。

---

## Task

异步任务状态机。**任务由系统创建**（Phase 2 worker），不暴露 HTTP create 端点。

### `GET /workspaces/{workspace_id}/tasks` ✅

列出 workspace 内的任务。

**查询参数**：
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `status` | string | - | 过滤状态：`queued` / `running` / `waiting_for_user` / `succeeded` / `failed` / `cancel_requested` / `cancelled` |
| `limit` | int | 100 | 1-200 |
| `offset` | int | 0 | |

**响应**：
```json
{
  "items": [TaskRead, ...],
  "total": 0,
  "limit": 100,
  "offset": 0
}
```

### `GET /tasks/{task_id}` ✅

获取单个任务（不需要 workspace_id，task 自带 workspace_id 字段）。

**响应**：
```json
{
  "id": "...",
  "workspace_id": "...",
  "task_type": "parse_pdf",
  "status": "queued",
  "progress": 0.0,
  "payload": {"paper_id": "..."},
  "result": null,
  "error": null,
  "celery_task_id": null,
  "is_deleted": false,
  "created_at": "...",
  "updated_at": "..."
}
```

### `POST /tasks/{task_id}/cancel` ✅

请求取消任务。从非终态转到 `cancel_requested`。

**响应**：`TaskRead`（status 变为 `cancel_requested`）

**错误**：
- `409 invalid_task_transition`：从终态（succeeded/cancelled）取消

### `POST /tasks/{task_id}/resume` ✅

从 `waiting_for_user` 恢复任务（用户完成 HITL 决策后调用）。

**请求体**（可选）：
```json
{
  "decision": {"accepted": true, "comment": "Looks good"}
}
```

**响应**：`TaskRead`（status 变为 `running`）

**错误**：
- `409 invalid_task_transition`：任务不在 `waiting_for_user` 状态

### `POST /tasks/{task_id}/retry` ✅

重试失败的任务。从 `failed` 转到 `queued`，清空 error 和 progress。

**错误**：
- `409 invalid_task_transition`：任务不在 `failed` 状态

---

## Timeline

自动记录的研究活动。**只读**——事件由系统在重要操作后自动写入。

### `GET /workspaces/{workspace_id}/timeline` ✅

列出 workspace 的 timeline 事件。

**查询参数**：
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `subject_type` | string | - | 过滤主体类型：`paper` / `task` / `workspace` 等 |
| `subject_id` | string | - | 过滤主体 ID |
| `event_type` | string | - | 过滤事件类型：`paper.created` / `task.succeeded` 等 |
| `limit` | int | 100 | 1-500 |
| `offset` | int | 0 | |

**响应**：
```json
{
  "items": [
    {
      "id": "...",
      "workspace_id": "...",
      "event_type": "paper.uploaded",
      "actor": "system",
      "subject_type": "paper",
      "subject_id": "...",
      "payload": {
        "title": "...",
        "filename": "paper.pdf",
        "size_bytes": 1234567,
        "artifact_id": "...",
        "auto_filled": ["authors", "year"],
        "page_count": 12
      },
      "summary": null,
      "created_at": "2026-07-19T10:00:00Z"
    }
  ],
  "total": 14,
  "limit": 100,
  "offset": 0
}
```

**已记录的事件类型**：
| event_type | 触发时机 |
|------------|---------|
| `paper.created` | POST /papers（元数据创建） |
| `paper.uploaded` | POST /papers/upload |
| `paper.pdf_attached` | POST /papers/{id}/upload-pdf |
| `paper.updated` | PATCH /papers/{id} |
| `paper.deleted` | DELETE /papers/{id} |
| `task.created` | TaskService.create() |
| `task.queued` | transition to queued（含 retry） |
| `task.running` | transition to running |
| `task.succeeded` | transition to succeeded |
| `task.failed` | transition to failed |
| `task.cancelled` | transition to cancelled |
| `task.waiting_for_user` | transition to waiting_for_user |

**后续 Phase 会加的事件**：
- `workspace.created` / `workspace.updated` / `workspace.archived` / `workspace.deleted`（Phase 4）
- `opportunity.proposed` / `opportunity.confirmed` / `opportunity.rejected`（Phase 5）
- `user_decision.recorded`（Phase 4 HITL）

---

## Knowledge

知识图谱。Phase 1b 只读——内容由 Phase 3 抽取 pipeline 写入。

### `GET /workspaces/{workspace_id}/knowledge` ✅ 🔒

列出 knowledge items。

**查询参数**：
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `type` | string | - | 过滤类型：`paper` / `method` / `task` / `dataset` / `claim` / `evidence` / `limitation` |
| `status` | string | - | 过滤状态：`extracted_candidate` / `evidence_backed_proposal` / `human_confirmed` 等 |
| `limit` | int | 50 | 1-200 |
| `offset` | int | 0 | |

**响应**：
```json
{
  "items": [
    {
      "id": "...",
      "workspace_id": "...",
      "type": "method",
      "canonical_name": "GNNExplainer",
      "content": {"description": "...", "inputs": [...]},
      "source_provenance": {"paper_id": "...", "agent_run_id": "..."},
      "created_by": "agent",
      "confidence": 0.85,
      "status": "evidence_backed_proposal",
      "version": 1,
      "is_deleted": false,
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "total": 0,
  "limit": 50,
  "offset": 0
}
```

### `GET /workspaces/{workspace_id}/knowledge/{item_id}` ✅ 🔒

获取单个 knowledge item。跨 workspace 返回 404。

### `GET /workspaces/{workspace_id}/knowledge/{item_id}/evidence` ✅ 🔒

获取某个 knowledge item 的所有 evidence span（证据回链到论文文本）。

**响应**：
```json
{
  "items": [
    {
      "id": "...",
      "knowledge_item_id": "...",
      "paper_id": "...",
      "artifact_id": "...",
      "chunk_index": 5,
      "start_char": 1234,
      "end_char": 1456,
      "text": "We propose GNNExplainer...",
      "relation": "supports",
      "confidence": 0.9
    }
  ],
  "total": 3
}
```

**`relation` 取值**：`supports` / `qualifies` / `contradicts`

### `GET /workspaces/{workspace_id}/knowledge/relations` ✅ 🔒

列出 knowledge relations（知识图谱的边）。

**查询参数**：
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `item_id` | string | - | 过滤涉及某个 item 的关系（作为 source 或 target） |
| `relation_type` | string | - | 过滤关系类型：`proposes` / `supports` / `contradicts` 等 |
| `limit` | int | 100 | 1-500 |
| `offset` | int | 0 | |

**响应**：
```json
{
  "items": [
    {
      "id": "...",
      "workspace_id": "...",
      "source_id": "...",
      "target_id": "...",
      "relation_type": "proposes",
      "confidence": 0.9,
      "payload": {},
      "is_deleted": false,
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "total": 0,
  "limit": 100,
  "offset": 0
}
```

### 计划的 Knowledge 写入端点 🚧

| 端点 | Phase | 说明 |
|------|-------|------|
| `POST /workspaces/{wid}/knowledge` | P3 | 手动创建 knowledge item（罕见，主要用于 Note） |
| `PATCH /workspaces/{wid}/knowledge/{id}` | P4 | HITL 编辑（用户修改 agent 抽取的内容） |
| `POST /workspaces/{wid}/knowledge/{id}/confirm` | P4 | 确认 candidate → confirmed |
| `POST /workspaces/{wid}/knowledge/{id}/reject` | P4 | 拒绝 candidate → rejected |
| `POST /workspaces/{wid}/knowledge/relations` | P3 | 手动添加关系 |
| `POST /workspaces/{wid}/knowledge/{id}/evidence` | P3 | 手动添加 evidence span |

---

## Retrieval（计划）

基于 Milvus 的向量检索。Phase 2-3 实现。

### `POST /workspaces/{workspace_id}/retrieval/search` 🚧

语义检索 workspace 内的论文 chunk。

**请求体**（计划）：
```json
{
  "query": "graph neural network explainability",
  "top_k": 10,
  "filters": {
    "paper_ids": ["..."],  // 限制在特定论文内
    "min_confidence": 0.5
  }
}
```

**响应**（计划）：
```json
{
  "items": [
    {
      "chunk_id": "...",
      "paper_id": "...",
      "chunk_index": 5,
      "text": "...",
      "score": 0.87,
      "evidence_span": {...}
    }
  ]
}
```

### `POST /workspaces/{workspace_id}/retrieval/similar-work` 🚧

针对某篇论文找相似工作。Phase 5 Discover Agent 用。

**请求体**：
```json
{
  "paper_id": "...",
  "top_k": 10
}
```

### `POST /workspaces/{workspace_id}/retrieval/counter-evidence` 🚧

针对某个 claim 找反证。Phase 5 Discover Agent 用。

**请求体**：
```json
{
  "claim_id": "...",
  "top_k": 10
}
```

---

## Discover Agent（计划）

Evidence-grounded Research Opportunity Discovery。Phase 5 实现。

### `POST /workspaces/{workspace_id}/discover/run` 🚧

启动一次 Discover Agent run（异步任务）。

**请求体**（计划）：
```json
{
  "scope": {
    "paper_ids": ["..."],  // 可选，限定论文范围
    "topic": "Self-Interpretable GNN"
  },
  "config": {
    "max_opportunities": 5,
    "include_counter_evidence": true
  }
}
```

**响应** `202 Accepted`：
```json
{
  "task_id": "...",
  "status": "queued"
}
```

### `GET /workspaces/{workspace_id}/discover/runs` 🚧

列出 Discover Agent 运行历史。

### `GET /workspaces/{workspace_id}/discover/runs/{run_id}` 🚧

获取某次 run 的详情（含生成的 opportunity 列表）。

---

## Opportunity（计划）

Research Opportunity 是 GapMind 的旗舰产出物。Phase 5 实现。

### `GET /workspaces/{workspace_id}/opportunities` 🚧

列出 opportunity（默认只返回 candidate 状态）。

**查询参数**：`status`（`candidate` / `confirmed` / `rejected` / `deferred`）

### `GET /workspaces/{workspace_id}/opportunities/{opp_id}` 🚧

获取单个 opportunity 详情。

**响应**（计划）：
```json
{
  "id": "...",
  "workspace_id": "...",
  "title": "Why post-hoc explainers fail on GNNs",
  "description": "...",
  "evidence_refs": [
    {
      "paper_id": "...",
      "evidence_span_id": "...",
      "relation": "supports",
      "text": "..."
    }
  ],
  "similar_work": [...],
  "counter_evidence": [...],
  "status": "candidate",
  "confidence": 0.8,
  "agent_run_id": "...",
  "created_at": "...",
  "confirmed_at": null
}
```

### `POST /workspaces/{workspace_id}/opportunities/{opp_id}/confirm` 🚧

用户确认 opportunity。`candidate` → `confirmed`。HITL 关键节点。

### `POST /workspaces/{workspace_id}/opportunities/{opp_id}/reject` 🚧

用户拒绝。

### `POST /workspaces/{workspace_id}/opportunities/{opp_id}/defer` 🚧

延后处理。

### `PATCH /workspaces/{workspace_id}/opportunities/{opp_id}` 🚧

用户编辑后确认（编辑 + 一次确认）。

### `POST /workspaces/{workspace_id}/opportunities/{opp_id}/convert` 🚧

把 confirmed opportunity 转化为：
- Research Question
- Hypothesis
- Validation Plan

**请求体**（计划）：
```json
{
  "target_type": "research_plan",
  "edits": {...}
}
```

---

## Research Plan（计划）

Phase 5-6 实现。

### `GET /workspaces/{workspace_id}/plans` 🚧

列出 research plans。

### `GET /workspaces/{workspace_id}/plans/{plan_id}` 🚧

获取单个 plan，含 research question / hypothesis / validation plan。

### `POST /workspaces/{workspace_id}/plans` 🚧

手动创建 plan（也可从 opportunity convert）。

### `PATCH /workspaces/{workspace_id}/plans/{plan_id}` 🚧

编辑 plan。

### `POST /workspaces/{workspace_id}/plans/{plan_id}/baseline-checklist` 🚧

根据 paper/GitHub repo 生成 Baseline Reproduction Checklist。Phase 6 P1 功能。

---

## 外部集成

### Semantic Scholar ✅

通过后端代理调用 Semantic Scholar Academic Graph API。API key 只在后端
使用，前端不会接触上游凭据。

#### `GET /papers/search` ✅

全局搜索 Semantic Scholar 论文候选结果，不会自动写入 Workspace。

**查询参数**：

`query`、`year_from`、`year_to`、`min_citation_count`、`open_access`、
`fields_of_study`、`publication_types`、`venue`、`sort`、`limit`、`offset`、`token`

`sort` 可选：`relevance`、`publicationDate:asc`、`publicationDate:desc`、
`citationCount:asc`、`citationCount:desc`。

相关性搜索使用 Semantic Scholar 的 `offset` 分页；按年份或引用数排序时使用
上游 bulk search 的 `token` 分页。

**响应**：候选论文列表（含 S2 元数据，未入库），字段保持 Semantic Scholar
的 camelCase 格式，例如 `paperId`、`citationCount`、`openAccessPdf`。

#### `POST /workspaces/{workspace_id}/papers/import-from-s2` ✅

把一个 S2 搜索结果导入为本地 Paper 元数据。PDF 不会自动下载，用户可以随后
使用已有的 PDF 上传/补传功能。

**请求体**：
```json
{
  "semantic_scholar_paper_id": "..."
}
```

### Model Gateway（内部，不暴露 HTTP）🚧

LLM/Embedding 调用通过 service 层封装，不直接暴露 HTTP 端点。Phase 3+ 会加：
- `POST /admin/model-calls`（只读，查询模型调用历史）

---

## 通用错误格式

所有错误响应遵循统一格式：

```json
{
  "detail": {
    "error": "error_code",
    "message": "Human-readable message"
  }
}
```

部分错误有额外字段：
- `409 invalid_task_transition`：`from_status`、`to_status`
- `422 validation_error`：`message` 是 Pydantic 错误数组

### 常见错误码

| HTTP | error code | 说明 |
|------|-----------|------|
| 400 | `invalid_file` | 非 PDF 文件 |
| 400 | `empty_file` | 空文件 |
| 404 | `workspace_not_found` | Workspace 不存在或已删除 |
| 404 | `paper_not_found` | Paper 不存在或已删除 |
| 404 | `artifact_not_found` | Artifact 不存在 |
| 404 | `task_not_found` | Task 不存在 |
| 404 | `knowledge_item_not_found` | Knowledge item 不存在 |
| 409 | `paper_already_has_pdf` | 论文已有 PDF，不能重复添加 |
| 409 | `invalid_task_transition` | Task 状态转换非法 |
| 413 | `file_too_large` | 文件超过 50MB |
| 422 | `validation_error` | 请求体校验失败 |

---

## 不做的接口 ❌

| 端点 | 原因 |
|------|------|
| `POST /tasks`（用户创建） | 任务由系统创建，不允许用户任意 spawn |
| `POST /timeline`（用户写） | Timeline 只能系统写 |
| `POST /knowledge/bulk`（用户批量导入） | Knowledge 只能通过抽取 pipeline 生成 |
| 用户认证端点 | MVP 单用户，无认证 |
| 团队/权限相关 | MVP 不做 |
| 任意 GitHub repo 运行 | 明确不做 |

---

## API 版本策略

当前所有端点在 `/api/v1` 下。未来 breaking change 会启用 `/api/v2` 并保留 v1 一段时间。

Phase 1b 的端点契约已稳定，后续 Phase 主要是**新增**端点，不会破坏现有契约。可能的例外：
- `PaperRead` 可能加字段（Phase 2 加 `parse_status`）——向前兼容
- `TaskRead` 可能加字段——向前兼容
- 某些 list 端点可能加新的 query 参数——向前兼容

---

## 测试覆盖

| 端点群 | 测试文件 | 测试数 |
|--------|---------|--------|
| Health | `test_health.py` | 3 |
| Workspace | `test_workspace_api.py` | 15 |
| Paper | `test_paper_api.py` | 10 |
| Paper (PDF metadata) | `test_pdf_metadata_api.py` | 13 |
| Task | `test_task_api.py` | 7 |
| Knowledge | `test_knowledge_api.py` | 4 |
| **总计** | | **52** |

运行：`cd backend && pytest tests/ -v`
