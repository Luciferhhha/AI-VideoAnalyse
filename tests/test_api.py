"""阶段一 API 测试：健康检查接口。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_content_type_json():
    resp = client.get("/health")
    assert resp.headers["content-type"].startswith("application/json")
