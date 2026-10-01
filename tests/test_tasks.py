"""阶段五测试：异步分析任务。

- 测试隔离：共用 `tests/conftest.py` 的 `client` fixture（临时 SQLite 库 + 临时
  上传目录）；后台任务经 `db.get_bind()` 使用同库的独立 Session，不污染真实 data/。
- 覆盖：创建立即返回 task_id（创建瞬间 pending）、状态流转 pending→running→success、
  失败路径（视频文件缺失 → failed + error_message，且服务不崩溃）、
  未预期异常只落库不上抛、视频/任务不存在 404。
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.database.repository import ResultRepository, TaskRepository
from app.services import task_service
from app.services.transcription_service import MockTranscriptionService
from tests.conftest import FIXTURES_DIR


def _upload(client: TestClient) -> int:
    content = (FIXTURES_DIR / "sample.mp4").read_bytes()
    resp = client.post(
        "/videos",
        files={"file": ("sample.mp4", content, "application/octet-stream")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_analyze_returns_task_id_immediately(client: TestClient) -> None:
    video_id = _upload(client)

    resp = client.post(f"/videos/{video_id}/analyze")

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert set(body) == {"task_id", "video_id", "status"}
    assert isinstance(body["task_id"], int)
    assert body["video_id"] == video_id
    assert body["status"] == "pending"  # 创建瞬间的状态：后台尚未执行


def test_task_flows_pending_running_success(
    client: TestClient, monkeypatch
) -> None:
    video_id = _upload(client)
    seen: list[str] = []
    real_placeholder = task_service.placeholder_analyze

    def spy(session, video) -> dict:
        # 分析开始执行那一刻的任务状态（由后台线程自己的 Session 读取）
        task = TaskRepository(session).list_by_video(video.id)[0]
        seen.append(task.status)
        return real_placeholder(session, video)

    monkeypatch.setattr(task_service, "placeholder_analyze", spy)

    task_id = client.post(f"/videos/{video_id}/analyze").json()["task_id"]

    resp = client.get(f"/tasks/{task_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert seen == ["running"], "占位分析执行时任务应已进入 running"
    assert body["status"] == "success"
    assert body["error_message"] is None
    assert body["created_at"] is not None
    assert body["started_at"] is not None
    assert body["finished_at"] is not None


def test_task_failure_records_error_and_service_survives(
    client: TestClient, tmp_path: Path
) -> None:
    video_id = _upload(client)
    # 删除落盘文件，模拟"分析执行时视频文件已不存在"
    removed = list((tmp_path / "uploads").glob("*_sample.mp4"))
    assert removed, "应先上传成功才谈得上文件缺失"
    for path in removed:
        path.unlink()

    resp = client.post(f"/videos/{video_id}/analyze")
    assert resp.status_code == 202, resp.text

    body = client.get(f"/tasks/{resp.json()['task_id']}").json()
    assert body["status"] == "failed"
    assert "视频文件不存在" in (body["error_message"] or "")
    assert body["started_at"] is not None
    assert body["finished_at"] is not None

    # 后台异常被捕获，服务没有崩溃：后续请求一切正常
    assert client.get("/health").status_code == 200
    assert client.get(f"/videos/{video_id}").status_code == 200


def test_unexpected_exception_marks_task_failed(
    client: TestClient, monkeypatch
) -> None:
    video_id = _upload(client)

    def boom(_session, _video):
        raise RuntimeError("boom in analysis")

    monkeypatch.setattr(task_service, "placeholder_analyze", boom)

    task_id = client.post(f"/videos/{video_id}/analyze").json()["task_id"]
    body = client.get(f"/tasks/{task_id}").json()

    assert body["status"] == "failed"
    assert "boom in analysis" in (body["error_message"] or "")
    assert client.get("/health").status_code == 200


def test_transcript_recorded_in_result(client: TestClient) -> None:
    """阶段七：占位链路已接入转写，AnalysisResult.transcript 落库（mock）。"""
    from app.database.database import get_db
    from app.main import app as fastapi_app

    video_id = _upload(client)
    task_id = client.post(f"/videos/{video_id}/analyze").json()["task_id"]
    body = client.get(f"/tasks/{task_id}").json()
    assert body["status"] == "success", body

    gen = fastapi_app.dependency_overrides[get_db]()
    session = next(gen)
    try:
        result = ResultRepository(session).get_by_task(task_id)
        assert result is not None
        assert result.transcript == MockTranscriptionService.MOCK_TEXT
        assert bool(result.summary)
    finally:
        gen.close()


def test_transcription_failure_marks_task_failed_with_reason(
    client: TestClient, monkeypatch
) -> None:
    """7.3：转写失败 → 任务 failed 且 error_message 记录原因，服务不崩溃。"""
    from app import config

    video_id = _upload(client)
    monkeypatch.setattr(config, "TRANSCRIPTION_PROVIDER", "mimo")
    monkeypatch.setattr(config, "MIMO_API_KEY", "")  # 模拟未填写 API Key

    task_id = client.post(f"/videos/{video_id}/analyze").json()["task_id"]
    body = client.get(f"/tasks/{task_id}").json()

    assert body["status"] == "failed"
    assert "MIMO_API_KEY" in (body["error_message"] or "")
    assert body["finished_at"] is not None
    assert client.get("/health").status_code == 200


def test_analyze_missing_video_returns_404(client: TestClient) -> None:
    resp = client.post("/videos/999/analyze")
    assert resp.status_code == 404
    assert "999" in resp.json()["detail"]
    # 没有创建任何任务
    assert client.get("/tasks/1").status_code == 404


def test_get_missing_task_returns_404(client: TestClient) -> None:
    resp = client.get("/tasks/999")
    assert resp.status_code == 404
    assert "999" in resp.json()["detail"]
