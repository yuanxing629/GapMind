# GapMind

面向 CS/AI 研究者的证据驱动、人机协同科研工作空间。

GapMind 服务于研究生、科研助理、导师与科研团队，支持三类核心任务：有证据的论文问答、研究机会核验，以及研究计划和代码草稿辅助。系统将论文证据、相似工作、反证、外部核验、Critic 收窄和人工确认组织成一个完整的研究流程。当前比赛 Demo 以计算机科学中的图机器学习/图神经网络作为示范领域，不构成产品范围限制。

正式产品入口是 `frontend/` 中的 React/Vite 应用。请按照下方启动命令访问 `http://localhost:5173`。仓库根目录可能存在未跟踪的历史静态原型文件，不属于 GapMind 正式入口或比赛交付物。

AI 输出默认是候选或草稿，不会自动成为科学事实；资料不足时会保留不确定性。代码生成默认只进行静态检查并提供预览/下载，不会自动执行。

## 技术栈

| 层次 | 技术 |
|------|------|
| 后端 | FastAPI + Python 3.11+ |
| 数据库 | PostgreSQL 15 |
| 向量数据库 | Milvus 2.x（单机模式） |
| 任务队列 | Redis 7 + Celery |
| 大语言模型 | DeepSeek（`deepseek-v4-flash`） |
| 向量模型 | SiliconFlow（`BAAI/bge-m3`） |
| 前端 | React 18 + TypeScript + Vite |
| UI 组件库 | Ant Design 5.x |
| 状态管理 | Zustand |
| 图可视化 | Cytoscape.js（知识图谱页面） |

## 仓库结构

```
GapMind/
├── backend/        # FastAPI 后端和 Celery Worker
├── frontend/       # React + Vite 前端
├── infra/          # 本地基础设施的 Docker Compose 配置
└── docs/           # 架构和规划文档
```

## 快速开始（Phase 0）

### 环境要求

- Python 3.11+
- Node.js 18+
- Docker + Docker Compose

### 1. 启动基础设施

```bash
cd infra
docker compose --env-file ../.env up -d   # 也可以直接执行 `up -d`，使用内置默认值
```

该命令会启动 PostgreSQL（5432）、Redis（6379）和 Milvus（19530）。

### 2. 配置并启动后端

```bash
cd backend
python -m venv .venv
# 或使用 uv venv --python 3.11.15 --seed --managed-python
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/Mac
pip install -r requirements.txt
# 在仓库根目录复制 .env.example 为 .env，然后填写所需密钥
alembic upgrade head
uvicorn app.main:app --reload
```

后端地址：http://localhost:8000

Swagger 文档：http://localhost:8000/docs

### 3. 启动 Celery Worker（单独终端）

```bash
cd backend
.venv\Scripts\activate
celery -A app.workers.celery_app worker --loglevel=info --pool=solo
```

**Windows 注意事项**：Celery 默认的 prefork 进程池在 Windows 上可能因操作系统安全策略限制 billiard 的 SemLock 而触发 `WinError 5: 拒绝访问`。`app/workers/celery_app.py` 会在 Windows 上自动切换为 `--pool=solo`，因此上面的命令可以直接使用。后续如需 I/O 并发，可先执行 `pip install gevent`，再切换为 `--pool=gevent`。

### 4. 配置并启动前端

```bash
cd frontend
# npm install
npm install --allow-remote=all
npm run gen:api
npm run dev
```

前端地址：http://localhost:5173

### 工作空间 Agent

工作空间 AI 助手面向 CS/AI 科研流程，支持有证据的论文问答、研究机会发现、研究计划生成和代码项目生成。Agent 任务由 Celery Worker 处理，因此必须启动 Redis 和 Worker。拉取包含 Agent 功能的数据库迁移后，请执行 `alembic upgrade head`，并重启 FastAPI 与 Celery。

生成的代码默认只提供预览和下载，绝不会自动执行。质量信号来自处理流程本身，包括纯 Python 静态检查（语法、依赖一致性和项目脚手架）以及研究计划覆盖度评估，并会如实报告已覆盖、部分覆盖、缺失项目和已知限制。

### 研究空白棋盘

GapMind 可以通过 Ollama 使用微调后的 Qwen3 Schema 3.0 抽取器，构建确定性的“方法 × 问题”研究空白棋盘，然后将未经核验的单元交给 Discover，进行外部新颖性和反证检查。在当前产品边界内，该模块属于抽取/标注组件，并不代表最终生成模型已经完成特定领域的 SFT。当前的安全边界和能力声明请参阅 [`docs/0824_scope_and_claims.md`](docs/0824_scope_and_claims.md)。

## 配置

稳定的运行时默认值保存在 [`config/gapmind.yaml`](config/gapmind.yaml) 中。后端启动时会先加载该文件，环境变量可以覆盖其中的任意配置，从而在保持环境文件简洁的同时支持不同部署环境。

复制 `.env.example` 为 `.env`，然后填写实际使用的凭据：

- `DEEPSEEK_API_KEY`：DeepSeek API 密钥
- `SILICONFLOW_API_KEY`：用于 BGE-m3 向量模型的 SiliconFlow API 密钥
- `SEMANTIC_SCHOLAR_API_KEY`：可选，用于提高 Semantic Scholar 的访问速率限制
- `AUTH_SESSION_SECRET`：staging/production 环境必填

机器本地的密钥或覆盖配置可以放在被 Git 忽略的 `.env.local` 中。后端会在 `.env` 之后加载 `.env.local`，Vite 也会自动加载该文件。不要把密钥写入 `config/gapmind.yaml`，也不要提交任何环境文件。

部署时仍可以通过 `APP_ENV`、`DATABASE_URL`、`REDIS_URL`、`CORS_ORIGINS` 和 `VITE_API_BASE_URL` 等环境变量覆盖配置。Docker Compose 使用命令中显式传入的 `--env-file`；未提供的基础设施配置会使用 Compose 内置的本地默认值。
