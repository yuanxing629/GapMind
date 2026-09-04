"""测试配置。

提供基于 SQLite 的单元测试引擎（速度快且不需要外部 DB），并覆盖 `get_db` dependency，
使每个测试获得独立的内存 session。

同时替换 `spawn_parse_pdf_task`，让解析器在同一个测试 session 中同步运行，因此 Phase 2
测试无需真实 Celery worker / Redis 即可验证完整流水线。
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.deps import get_db
from app.db.base import Base
from app.db.models import *  # noqa: F401,F403  (registers all models on Base.metadata)
from app.main import app


@pytest.fixture(autouse=True)
def _stub_milvus(monkeypatch):
    """为每个测试将 Milvus client 替换为无操作 MagicMock。

    测试 Milvus 行为的用例应使用自己的 fake 覆盖该 fixture。默认实现不执行操作并吞掉调用，
    因此只关注 DB 状态的测试（例如 paper API 测试）不必感知 Milvus 的存在。

    RG-6 增加了 paper.soft_delete 到 Milvus 的传播；否则原本通过的测试会访问真实（或不
    存在的）Milvus 并因 RPC 错误失败。所有调用统一经过该 stub，以保持测试套件稳定。
    """
    fake = MagicMock(name="milvus_client")
    fake.get_existing_chunk_ids.return_value = set()
    fake.search.return_value = []
    fake.insert_chunks.return_value = 0

# 同时 patch retrieval.service 和 paper.service 中的模块级 milvus_client
#（两者都通过 `from ... import milvus_client` 持有自己的引用，因此分别替换模块属性即可）。
    from app.domains.paper import service as paper_service_module
    from app.domains.retrieval import service as retrieval_service_module

    monkeypatch.setattr(retrieval_service_module, "milvus_client", fake)
    monkeypatch.setattr(paper_service_module, "milvus_client", fake)
    return fake


@pytest.fixture(scope="function")
def db_session() -> Generator[Session, None, None]:
    """为每个测试生成全新的内存 SQLite session。

    StaticPool + check_same_thread=False 允许 TestClient（可能运行在其他线程）共享同一个
    内存连接。
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    connection = engine.connect()
    transaction = connection.begin()
# 每个 Session 事务使用一个 savepoint，使应用的 rollback() 行为接近生产环境，
# 同时不会回滚 fixture 的外层初始化。
    session = TestingSessionLocal(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    yield session

    session.close()
    transaction.rollback()
    connection.close()
    engine.dispose()


@pytest.fixture(scope="function")
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """将 get_db dependency 覆盖为使用测试 session 的 TestClient。

    同时替换 spawn_parse_pdf_task 使解析器同步运行，因此无需 Celery/Redis 即可验证完整的
    Phase 2 流水线。
    """

    def _override_get_db() -> Generator[Session, None, None]:
        try:
            yield db_session
        finally:
# 测试 session 由 db_session fixture 关闭，这里无需额外处理。
            pass

    app.dependency_overrides[get_db] = _override_get_db

# patch spawn_parse_pdf_task，使其在测试 session 中同步运行，而不是派发到 Celery。
# paper service 会在方法内部延迟导入该名称，因此替换模块属性即可。
    with patch(
        "app.workers.tasks.parse_pdf.spawn_parse_pdf_task",
        new=_sync_spawn_parse_pdf,
    ):
        with patch(
            "app.workers.tasks.extract_knowledge.spawn_extract_knowledge",
            return_value="test-extraction-task",
        ):
            with patch(
                "app.workers.tasks.embed_chunks.spawn_embed_chunks",
                return_value="test-embed-task",
            ):
                with TestClient(app) as c:
                    yield c

    app.dependency_overrides.clear()


def _sync_spawn_parse_pdf(db: Session, paper_id: str, workspace_id: str) -> str:
    """spawn_parse_pdf_task 的测试替身。

    创建 Task 行（供 API/UI 展示），并在同一个数据库会话中同步运行解析器，
    使测试可以直接看到最终
    立即看到最终 state。
    """
    from app.domains.task.schemas import TaskCreate
    from app.domains.task.service import TaskService
    from app.workers.tasks.parse_pdf import _run_parse_pdf

    task = TaskService(db).create(
        TaskCreate(
            workspace_id=workspace_id,
            task_type="parse_pdf",
            payload={"paper_id": paper_id},
        )
    )
# 在测试 session 中同步运行。
    _run_parse_pdf(db, task.id)
    return task.id
