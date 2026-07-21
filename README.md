# GapMind

Evidence-grounded, Human-in-the-Loop AI Research Workspace.

## Current Status

**Phase 0: Infrastructure Setup** - in progress.

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
| Graph viz | Cytoscape.js (planned) |

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
docker compose up -d
```

This starts PostgreSQL (5432), Redis (6379), and Milvus (19530).

### 2. Backend setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/Mac
pip install -r requirements.txt
copy ..\infra\.env.example .env  # then edit .env with your keys
alembic upgrade head
uvicorn app.main:app --reload
```

Backend: http://localhost:8000
Swagger: http://localhost:8000/docs

### 3. Celery worker (separate terminal)

```bash
cd backend
.venv\Scripts\activate
celery -A app.workers.celery_app worker --loglevel=info
```

**Windows note**: Celery's default prefork pool crashes on Windows with
`WinError 5: 拒绝访问` (billiard's SemLock blocked by OS security policy).
`app/workers/celery_app.py` auto-switches to `--pool=solo` on Windows, so the
plain command above works out of the box. For I/O-bound concurrency later,
switch to `--pool=gevent` (after `pip install gevent`).

### 4. Frontend setup

```bash
cd frontend
npm install
npm run dev
```

Frontend: http://localhost:5173

## Development Phases

- **Phase 0** - Infrastructure + project skeleton (current)
- **Phase 1** - Core Domain models (Workspace, Artifact, Paper, Knowledge, Task, Timeline)
- **Phase 2** - Task Runtime + PDF parsing pipeline
- **Phase 3** - Knowledge extraction + Milvus retrieval
- **Phase 4** - Timeline + Human-in-the-Loop
- **Phase 5** - Discover Agent (Opportunity discovery)
- **Phase 6** - Frontend core pages
- **Phase 7** - Knowledge graph visualization

See `docs/plans.md` for full architecture and MVP scope.

## Environment Variables

Copy `infra/.env.example` to `backend/.env` and fill in:

- `DEEPSEEK_API_KEY` - Deepseek API key
- `SILICONFLOW_API_KEY` - SiliconFlow API key (for BGE-m3 embedding)
- `SEMANTIC_SCHOLAR_API_KEY` - (optional) for higher rate limits
- `POSTGRES_*`, `REDIS_*`, `MILVUS_*` - infra connection settings
