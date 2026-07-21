# GapMind 数据库设计

> 当前版本：Phase 1b（迁移 `0003_phase1b`）
> 数据库：PostgreSQL 15
> 最后更新：2026-07-19

## 概览

GapMind 使用 **PostgreSQL** 存储所有结构化业务数据，配合 **本地文件系统**（PDF 等大文件）、**Redis**（Celery 任务队列）、**Milvus**（向量检索，Phase 2 接入）组成完整存储栈。

### 表清单

| # | 表名 | Phase | 用途 | 当前数据量 |
|---|------|-------|------|-----------|
| 1 | `workspaces` | 1a | 研究工作空间 + Research Profile | 6 |
| 2 | `artifacts` | 1b | 通用文件记录（PDF / 解析文本 / 报告） | 6 |
| 3 | `papers` | 1b | 论文元数据 + 主 PDF 引用 | 7 |
| 4 | `tasks` | 1b | 异步任务状态机 | 0 |
| 5 | `timeline_events` | 1b | 自动记录的研究活动 | 14 |
| 6 | `knowledge_items` | 1b（只读） | 17 种知识类型核心存储 | 0 |
| 7 | `knowledge_relations` | 1b（只读） | 知识图谱的边（显式关系） | 0 |
| 8 | `evidence_spans` | 1b（只读） | 证据回链（知识←论文文本片段） | 0 |
| - | `alembic_version` | 基础设施 | Alembic 迁移版本追踪 | 1 |

### 通用设计模式

每张业务表都遵循以下约定：

- **UUID 主键**（`VARCHAR(36)`）：跨 DB 可移植，未来分布式部署无需改动
- **`workspace_id` 外键 + 索引**：所有数据按 workspace 隔离（单用户 MVP 的"虚拟租户"边界）
- **`is_deleted` 软删除**：科研数据需要审计追溯，从不硬删除
- **`created_at` / `updated_at`**：带时区的时间戳，由 SQLAlchemy 自动维护（`server_default=now()`, `onupdate=now()`)
- **JSON 字段承载异构数据**：`content`、`payload`、`source_provenance`、`authors`、`keywords` 等

### 关键设计选择

| 选择 | 理由 |
|------|------|
| PostgreSQL 而非 SQLite | 支持 JSON 操作、并发写入、未来多用户扩展 |
| UUID 字符串而非原生 UUID 类型 | 跨 DB 可移植，避免 dialect 差异（SQLite 测试 vs PostgreSQL 生产） |
| 软删除而非硬删除 | 科研数据需要完整审计链，Timeline 引用不能悬空 |
| JSON 字段而非子表 | 17 种知识类型异构 schema，子表会爆炸 |
| 关系表而非图数据库 | MVP 只需"查邻居"查询，SQL 索引足够；plans.md 明确"暂不需要图数据库" |
| 状态机在 service 层校验 | DB CHECK 约束表达不了复杂转换规则（如 `failed → queued` retry） |
| 知识类型用字符串而非枚举 | 未来加新类型（opportunity/hypothesis 等）无需迁移 |

---

## 表结构详解

### 1. `workspaces`

**核心表，所有其他表通过 `workspace_id` FK 关联回它**。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | VARCHAR(36) | PK | UUID 主键 |
| `name` | VARCHAR(255) | NOT NULL | 工作空间名称 |
| `description` | TEXT | | 描述 |
| `topic` | TEXT | | 研究主题 |
| `keywords` | JSON | NOT NULL, default `[]` | 关键词列表 |
| `goals` | TEXT | | 研究目标 |
| `constraints` | TEXT | | 约束条件 |
| `active_questions` | JSON | NOT NULL, default `[]` | 活跃研究问题列表 |
| `is_archived` | BOOLEAN | NOT NULL, default false | 归档标志 |
| `is_deleted` | BOOLEAN | NOT NULL, default false | 软删除标志 |
| `created_at` | TIMESTAMP | NOT NULL, default now() | |
| `updated_at` | TIMESTAMP | NOT NULL, default now() | |

**索引**：`is_archived`, `is_deleted`

**设计决策**：Research Profile（topic/keywords/goals/constraints/active_questions）**内联在 workspace 表**而非单独建 `research_profiles` 表。理由：Profile 和 Workspace 是 1:1，字段简单，分表只会多一次 join。未来 Profile 字段膨胀再拆。

**归档 vs 删除的区别**：
- **归档**（`is_archived=true`）：暂时搁置，可恢复，列表可通过 `include_archived=true` 查看
- **删除**（`is_deleted=true`）：逻辑删除，从所有常规查询消失，但行保留供审计

---

### 2. `artifacts`

**通用文件管理表。一个 artifact = 一个文件**。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | VARCHAR(36) | PK | UUID |
| `workspace_id` | VARCHAR(36) | FK→workspaces.id, NOT NULL, CASCADE | 所属工作空间 |
| `kind` | VARCHAR(32) | NOT NULL | 文件类型：`pdf` / `parsed_text` / `chunk_index` / `report` |
| `file_path` | VARCHAR(1024) | NOT NULL | 相对存储路径 |
| `original_filename` | VARCHAR(512) | | 用户上传时的原文件名 |
| `mime_type` | VARCHAR(128) | | MIME 类型 |
| `size_bytes` | BIGINT | NOT NULL, default 0 | 文件大小 |
| `is_deleted` | BOOLEAN | NOT NULL, default false | 软删除 |
| `created_at` / `updated_at` | TIMESTAMP | NOT NULL | |

**索引**：`workspace_id`, `kind`, `is_deleted`

**文件存储路径**：
```
storage/workspaces/<UUID前2位>/<完整UUID>/artifacts/<token>.<ext>
```

**设计决策**：artifact 和 paper **分开两张表**（不是 paper 里直接存 PDF 路径）。原因：未来一篇 paper 可能关联多个 artifact（原始 PDF + 解析文本 + chunk 索引 + 生成的报告），独立表支持 1:N。

---

### 3. `papers`

**论文元数据 + 主 PDF 引用**。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | VARCHAR(36) | PK | UUID |
| `workspace_id` | VARCHAR(36) | FK→workspaces.id, NOT NULL, CASCADE | 所属 |
| `primary_artifact_id` | VARCHAR(36) | FK→artifacts.id, SET NULL | 主 PDF（可空） |
| `title` | TEXT | NOT NULL | 标题 |
| `authors` | JSON | NOT NULL, default `[]` | 作者列表 `["Alice", "Bob"]` |
| `year` | INTEGER | | 发表年份（1900-2100） |
| `abstract` | TEXT | | 摘要 |
| `doi` | VARCHAR(255) | | DOI |
| `arxiv_id` | VARCHAR(64) | | arXiv ID |
| `source` | VARCHAR(32) | NOT NULL, default `manual` | 来源：`manual` / `semantic_scholar` |
| `external_paper_id` | VARCHAR(128) | | 外部系统 ID（如 S2 paperId） |
| `is_deleted` | BOOLEAN | NOT NULL, default false | 软删除 |
| `created_at` / `updated_at` | TIMESTAMP | NOT NULL | |

**索引**：`workspace_id`, `primary_artifact_id`, `is_deleted`

**设计决策**：
- `authors` 用 JSON 列而非关联表——作者数量少（通常 < 20），不需要按作者查询
- `primary_artifact_id` 可空——支持"先加元数据后补 PDF"流程（`POST /papers` 创建 + `POST /papers/{id}/upload-pdf` 补传）
- Phase 2 会加字段：`parse_status`, `parsed_at`, `chunk_count`

---

### 4. `tasks`

**异步任务状态机**。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | VARCHAR(36) | PK | UUID |
| `workspace_id` | VARCHAR(36) | FK→workspaces.id, CASCADE | 所属（可空：全局任务） |
| `task_type` | VARCHAR(64) | NOT NULL | `parse_pdf` / `embed_chunks` / `extract_knowledge` 等 |
| `status` | VARCHAR(32) | NOT NULL, default `queued` | 见下方状态机 |
| `progress` | DOUBLE PRECISION | NOT NULL, default 0.0 | 0.0 - 1.0 |
| `payload` | JSON | NOT NULL, default `{}` | 输入参数 + 中间状态 |
| `result` | JSON | | 成功结果 |
| `error` | TEXT | | 失败原因 |
| `celery_task_id` | VARCHAR(128) | | Celery 后端任务 ID |
| `is_deleted` | BOOLEAN | NOT NULL, default false | 软删除 |
| `created_at` / `updated_at` | TIMESTAMP | NOT NULL | |

**索引**：`workspace_id`, `task_type`, `status`, `celery_task_id`, `is_deleted`

**状态机**：
```
queued ─→ running ─→ waiting_for_user ─→ running (resume)
                   ├─→ succeeded (terminal)
                   ├─→ failed ──→ queued (retry)
                   └─→ cancel_requested ─→ cancelled (terminal)
```

状态转换规则在 [task/service.py](../backend/app/domains/task/service.py) 的 `ALLOWED_TRANSITIONS` 字典中定义，由 service 层校验。

---

### 5. `timeline_events`

**自动记录的研究活动流水**。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | VARCHAR(36) | PK | UUID |
| `workspace_id` | VARCHAR(36) | FK→workspaces.id, NOT NULL, CASCADE | 所属 |
| `event_type` | VARCHAR(64) | NOT NULL | `paper.created` / `task.succeeded` 等 |
| `actor` | VARCHAR(16) | NOT NULL, default `system` | `system` / `agent` / `user` |
| `subject_type` | VARCHAR(32) | | 主体类型：`paper` / `task` 等 |
| `subject_id` | VARCHAR(36) | | 主体 ID（通用指针） |
| `payload` | JSON | NOT NULL, default `{}` | 事件特有数据 |
| `summary` | TEXT | | 人类可读摘要 |
| `created_at` / `updated_at` | TIMESTAMP | NOT NULL | |

**索引**：`workspace_id`, `event_type`, `subject_type`, `subject_id`

**已记录的事件类型**：
- `paper.created` / `paper.uploaded` / `paper.updated` / `paper.deleted` / `paper.pdf_attached`
- `task.created` / `task.running` / `task.succeeded` / `task.failed` / `task.cancelled` / `task.queued`

**设计决策**：用 `subject_type + subject_id` 通用指针而非每种事件一张子表。原因：事件类型会持续增长（Phase 4 加 HITL 决策事件、Phase 5 加 opportunity 事件），每加一种就建表不可维护。

**重要约束**：Timeline 事件只能由系统写入（通过 `TimelineService.record()`），用户从不直接创建。

---

### 6. `knowledge_items`

**17 种知识类型的核心存储**（Phase 1b 支持核心 7 种）。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | VARCHAR(36) | PK | UUID |
| `workspace_id` | VARCHAR(36) | FK, CASCADE | 所属 |
| `type` | VARCHAR(32) | NOT NULL | 见下方类型列表 |
| `canonical_name` | TEXT | NOT NULL | 规范化名称 |
| `content` | JSON | NOT NULL, default `{}` | 结构化内容（按 type 不同 schema） |
| `source_provenance` | JSON | NOT NULL, default `{}` | 来源（哪篇 paper、哪个 agent run） |
| `created_by` | VARCHAR(16) | NOT NULL, default `system` | `user` / `agent` / `system` |
| `confidence` | DOUBLE PRECISION | NOT NULL, default 0.0 | 0.0-1.0 |
| `status` | VARCHAR(32) | NOT NULL, default `extracted_candidate` | 见下方状态流转 |
| `version` | INTEGER | NOT NULL, default 1 | 版本号（HITL 编辑后递增） |
| `is_deleted` | BOOLEAN | NOT NULL, default false | 软删除 |
| `created_at` / `updated_at` | TIMESTAMP | NOT NULL | |

**索引**：`workspace_id`, `type`, `status`, `is_deleted`

**Phase 1b 支持的 7 种类型**：
`paper` / `method` / `task` / `dataset` / `claim` / `evidence` / `limitation`

**后续 Phase 会加的类型**（不改表，只扩 type 值）：
`opportunity` / `research_question` / `hypothesis` / `research_plan` / `citation` / `note` / `code_repository` / `baseline` / `metric` / `idea` / `future_work`

**状态流转**（plans.md 定义）：
```
raw_source → extracted_candidate → evidence_backed_proposal
          → human_confirmed → experiment_validated
                          → deprecated | rejected | invalidated
```

**设计决策**：
- `content` 用 JSON 而非按 type 建不同表——17 种类型每种都建表会爆炸。schema 由 service 层（Phase 3）的 validator 保证
- Phase 1b 没有写入逻辑，Phase 3 抽取 pipeline 才会填充

---

### 7. `knowledge_relations`

**知识图谱的边（显式关系）**。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | VARCHAR(36) | PK | UUID |
| `workspace_id` | VARCHAR(36) | FK, CASCADE | 所属 |
| `source_id` | VARCHAR(36) | FK→knowledge_items.id, CASCADE | 起点 |
| `target_id` | VARCHAR(36) | FK→knowledge_items.id, CASCADE | 终点 |
| `relation_type` | VARCHAR(64) | NOT NULL | 见下方关系类型 |
| `confidence` | DOUBLE PRECISION | NOT NULL, default 0.0 | 置信度 |
| `payload` | JSON | NOT NULL, default `{}` | 关系特有数据 |
| `is_deleted` | BOOLEAN | NOT NULL, default false | 软删除 |
| `created_at` / `updated_at` | TIMESTAMP | NOT NULL | |

**索引**：`workspace_id`, `source_id`, `target_id`, `relation_type`, `is_deleted`

**关系类型**（plans.md 定义）：
- `proposes`（Paper→Method）
- `addresses`（Paper→Task）
- `evaluates_on`（Paper→Dataset）
- `compares_with`（Paper→Method）
- `claims`（Paper→Claim）
- `mentions_limitation`（Paper→Limitation）
- `suggests`（Paper→FutureWork）
- `extends`（Method→Method）
- `supports` / `qualifies` / `contradicts`（Evidence→Claim）
- `derived_from`（Opportunity→Evidence）
- `related_to`（通用）

**设计决策**：用关系表而非图数据库。原因：MVP 不需要图遍历算法（GNN 留到后期），只需"查某个节点的所有邻居"——SQL 的 WHERE + 索引完全够用。

---

### 8. `evidence_spans`

**证据回链：哪个知识项来自哪篇论文的哪段文字**。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | VARCHAR(36) | PK | UUID |
| `workspace_id` | VARCHAR(36) | FK, CASCADE | 所属 |
| `knowledge_item_id` | VARCHAR(36) | FK→knowledge_items.id, CASCADE | 知识项 |
| `paper_id` | VARCHAR(36) | FK→papers.id, CASCADE | 论文 |
| `artifact_id` | VARCHAR(36) | FK→artifacts.id, SET NULL | 具体 artifact |
| `chunk_index` | INTEGER | | chunk 序号（Phase 2 分块后） |
| `start_char` / `end_char` | INTEGER | | 字符偏移 |
| `text` | TEXT | | 证据原文 |
| `relation` | VARCHAR(16) | NOT NULL, default `supports` | `supports` / `qualifies` / `contradicts` |
| `confidence` | DOUBLE PRECISION | NOT NULL, default 0.0 | 置信度 |
| `created_at` / `updated_at` | TIMESTAMP | NOT NULL | |

**索引**：`workspace_id`, `knowledge_item_id`, `paper_id`

**设计决策**：`relation` 字段区分三种证据关系——支持/限定/反驳。这是 GapMind 的核心差异化（plans.md 要求"必须展示反证"），evidence_spans 表从设计上就支持反证，而不只是"支持"。

---

## 实体关系图（ER 简图）

```
workspaces (1) ──── (N) artifacts
            │
            ├──── (N) papers ──── (1) artifacts [primary_artifact_id]
            │
            ├──── (N) tasks
            │
            ├──── (N) timeline_events
            │
            ├──── (N) knowledge_items ──── (N) knowledge_relations
            │                    │              (self-referential via source_id/target_id)
            │                    │
            └──── (N) evidence_spans ──── (1) papers
                        │
                        └──── (1) artifacts
```

**所有"多"端表都通过 `workspace_id` 回指 workspaces**，形成 workspace-scoped 隔离。

---

## 迁移历史

| 迁移 | 内容 |
|------|------|
| `0001_initial` | 空基线（Phase 0） |
| `0002_workspaces` | 创建 `workspaces` 表（Phase 1a） |
| `0003_phase1b` | 创建 7 张表：artifacts / papers / tasks / timeline_events / knowledge_items / knowledge_relations / evidence_spans |

迁移文件位于 [backend/alembic/versions/](../backend/alembic/versions/)。

**常用命令**：
```bash
cd backend
alembic upgrade head       # 应用所有待执行迁移
alembic current            # 查看当前版本
alembic history            # 查看迁移历史
alembic downgrade -1       # 回退一个版本（慎用）
```

---

## 后续 Phase 的 schema 演进

| Phase | 变更 |
|-------|------|
| **Phase 2** | `papers` 加 `parse_status` / `parsed_at` / `chunk_count` 字段；`tasks` 表开始有真实数据；Milvus 建 `paper_chunks` collection（不在 PostgreSQL） |
| **Phase 3** | `knowledge_items` / `knowledge_relations` / `evidence_spans` 开始有数据；可能加 `agent_runs` 表记录 LLM 调用历史 |
| **Phase 4** | 可能加 `user_decisions` 表记录 HITL 决策（Accept/Edit/Reject/Defer） |
| **Phase 5** | `knowledge_items.type` 扩展支持 `opportunity` / `research_question` / `hypothesis` / `research_plan`（不改表结构） |

**重要**：Phase 1b 的表设计已为后续 Phase 预留扩展空间。`type` 和 `task_type` 等字段用字符串而非枚举，加新值不需要迁移。
