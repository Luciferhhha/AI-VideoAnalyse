"""阶段一 API 测试：健康检查接口。

注意：必须用 `with TestClient(app)` 触发 lifespan（init_db / ensure_dirs），
否则应用启动逻辑在测试中不会执行 —— 见 development-log 阶段三问题记录。
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_returns_ok(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_content_type_json(client: TestClient):
    resp = client.get("/health")
    assert resp.headers["content-type"].startswith("application/json")


def test_lifespan_creates_database_tables(client: TestClient) -> None:
    """lifespan 执行 init_db 后，真实库中应存在全部业务表。"""
    from sqlalchemy import inspect

    from app.database.database import engine

    tables = set(inspect(engine).get_table_names())
    assert {"videos", "analysis_tasks", "analysis_results"} <= tables
