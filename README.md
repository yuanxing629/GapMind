# GapMind

Evidence-grounded, Human-in-the-Loop research workspace for CS/AI researchers.

GapMind 是面向 CS/AI 科研的证据驱动研究工作空间，服务研究生、科研助理、导师与科研团队，支持三类核心任务：有证据的论文问答、研究机会核验、研究计划和代码草稿辅助。系统将论文证据、相似工作、反证、外部核验、Critic 收窄和人工确认组织成一个研究流程。当前比赛 Demo 以计算机科学—图机器学习/图神经网络作为示范领域，不构成产品范围限制。

正式产品入口是 `frontend/` 中的 React/Vite 应用；请按下方启动命令访问 `http://localhost:5173`。仓库根目录可能存在未跟踪的历史静态原型文件，不属于 GapMind 正式入口或比赛交付物。


AI 输出默认是候选或草稿，不自动成为科学事实；资料不足时会保留不确定性，代码生成默认只做静态检查和预览/下载，不自动执行。

## Tech Stack

| Layer | Technology |
|------|------|
| Backend | FastAPI + Python 3.11+ |
| Database | PostgreSQL 15 |
| Vector DB | Milvus 2.x (standalone) |
| Queue | Redis 7 + Celery |
| LLM | Deepseek (`deepseek-v4-flash`) |
| Embedding | SiliconFlow (`BAAI/bge-m3`) |
| Frontend | React 18 + TypeScript + Vite |
| UI | Ant Design 5.x |
| State | Zustand |
| Graph viz | Cytoscape.js (knowledge graph page) |

## Repository Layout

```
GapMind/
├── backend/        # FastAPI + Celery workers
├── frontend/       # React + Vite
├── infra/          # Docker Compose for local infra
└── docs/           # Architecture and planning docs
```

## Quick Start (Phase 0)

### Prerequisites

- Python 3.11+
- Node.js 18+
- Docker + Docker Compose

### 1. Start infrastructure

```bash
cd infra
docker compose --env-file ../.env up -d   # or plain `up -d` to use built-in defaults
```

This starts PostgreSQL (5432), Redis (6379), and Milvus (19530).

### 2. Backend setup

```bash
cd backend
python -m venv .venv
# or uv venv --python 3.11.15 --seed --managed-python
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/Mac
pip install -r requirements.txt
# from the repo root: copy .env.example .env  (then edit .env with your keys)
alembic upgrade head
uvicorn app.main:app --reload
```

Backend: http://localhost:8000
Swagger: http://localhost:8000/docs

### 3. Celery worker (separate terminal)

```bash
cd backend
.venv\Scripts\activate
celery -A app.workers.celery_app worker --loglevel=info --pool=solo
```

**Windows note**: Celery's default prefork pool crashes on Windows with
`WinError 5: 拒绝访问` (billiard's SemLock blocked by OS security policy).
`app/workers/celery_app.py` auto-switches to `--pool=solo` on Windows, so the
plain command above works out of the box. For I/O-bound concurrency later,
switch to `--pool=gevent` (after `pip install gevent`).

### 4. Frontend setup

```bash
cd frontend
# npm install
npm install --allow-remote=all
npm run gen:api
npm run dev
```

Frontend: http://localhost:5173

### Workspace Agents

The workspace AI assistant is scoped to the CS/AI research workflow. It supports evidence-grounded Q&A, research-opportunity discovery, research-plan generation, and code-project generation. Agent runs are processed by the Celery worker, so Redis and the worker must be running. After pulling migrations that add Agent support, run `alembic upgrade head` and restart both FastAPI and Celery.

Generated code is previewed and downloaded by default; it is never executed automatically. Quality signals come from the pipeline itself: a pure-Python static review (syntax gate, dependency consistency, scaffolding) and a plan-coverage rubric that reports covered/partial/missing items and known gaps honestly.

### Research Gap Board

GapMind can use a fine-tuned Qwen3 Schema 3.0 extractor through Ollama to build a deterministic method-by-problem board, then hand unverified cells to Discover for external novelty and counter-evidence checks. In the current product boundary, this is an extraction/annotation component rather than a claim that the final generation model has completed domain SFT. See [`docs/0824_scope_and_claims.md`](docs/0824_scope_and_claims.md) for the current safety and claim boundaries.

## Environment Variables

A single `.env` at the repo root is shared by all three runtimes — copy `.env.example` (repo root) to `.env` and fill in:

- `DEEPSEEK_API_KEY` - Deepseek API key
- `DEEPSEEK_VISION_MODEL` - image-capable model used only for chat messages with images (defaults to `deepseek-v4-flash-vision-exp`)
- `SILICONFLOW_API_KEY` - SiliconFlow API key (for BGE-m3 embedding)
- `SEMANTIC_SCHOLAR_API_KEY` - (optional) for higher rate limits
- `DEEPSEEK_BACKUP_*` - (optional) backup OpenAI-compatible endpoint; automatic failover when the primary LLM fails (all three fields must be set)
- `GAP_EXTRACTOR_*` - fine-tuned gap-board model via Ollama (defaults usually suffice)
- `VITE_API_BASE_URL` - frontend API base (vite reads `VITE_*` from the same file)
- `POSTGRES_*`, `REDIS_*`, `MILVUS_*` - infra connection settings (also used by `docker compose --env-file ../.env` from `infra/`)
