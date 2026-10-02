"""阶段十六测试：控制面板（页面 + 面板端点）。

- 面板入口 `GET /`：返回单文件静态页，且 `/docs` 仍为 Swagger 不受影响。
- `GET /videos`：空列表 → 上传后 1 行 → mock 分析后关键帧实数与章节数。
- `GET /tasks`：任务列表（新→旧，带视频文件名）。
- `GET /logs`：结构、limit/level 参数与 422 校验。
- `GET /settings`：provider/限额快照，且**绝不回传 API Key 明文**。

隔离同 `tests/conftest.py` 的 `client` fixture：临时 SQLite + 临时上传/输出目录 + mock provider。
"""

import logging

from fastapi.testclient import TestClient

from app.services.analysis_service import MockAnalysisLLM
from app.services.transcription_service import MockTranscriptionService
from tests.conftest import FIXTURES_DIR

PANEL_KEYS = ["视频上传", "关键帧", "分析结果", "API 控制", "日志流程"]


def _upload(client: TestClient) -> int:
    content = (FIXTURES_DIR / "sample.mp4").read_bytes()
    resp = client.post(
        "/videos",
        files={"file": ("sample.mp4", content, "application/octet-stream")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _analyze(client: TestClient, video_id: int) -> int:
    resp = client.post(f"/videos/{video_id}/analyze")
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["task_id"]
    body = client.get(f"/tasks/{task_id}").json()
    assert body["status"] == "success", body
    return task_id


# ---------------------------------------------------------------- 页面


def test_panel_index_serves_html(client: TestClient) -> None:
    """GET / 返回面板 HTML，五大区块齐全。"""
    resp = client.get("/")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/html")
    for key in PANEL_KEYS:
        assert key in resp.text, f"面板缺少区块：{key}"
    # 纯静态单页：不依赖外部 CDN
    assert "http://" not in resp.text and "https://" not in resp.text


def test_panel_index_missing_file_returns_404(
    client: TestClient, monkeypatch
) -> None:
    from pathlib import Path

    from app import main as main_module

    monkeypatch.setattr(main_module, "PANEL_HTML", Path("does/not/exist.html"))
    resp = client.get("/")
    assert resp.status_code == 404
    assert "控制面板页面缺失" in resp.json()["detail"]


def test_docs_still_swagger(client: TestClient) -> None:
    """/docs 未被面板改造占用，仍是 Swagger UI。"""
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert "swagger" in resp.text.lower()


# ---------------------------------------------------------------- 视频列表


def test_videos_list_empty_then_after_upload_and_analyze(client: TestClient) -> None:
    assert client.get("/videos").json() == []

    video_id = _upload(client)
    rows = client.get("/videos").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == video_id
    assert row["filename"].endswith(".mp4")
    assert row["duration"] is not None
    assert row["keyframe_count"] == 0  # 未分析，无关键帧
    assert row["chapter_count"] == 0
    assert row["task_count"] == 0
    assert row["latest_task_id"] is None
    assert row["latest_task_status"] is None

    _analyze(client, video_id)
    rows = client.get("/videos").json()
    assert len(rows) == 1
    row = rows[0]
    # 关键帧：data/outputs/{id}/frames 磁盘实数（interval=5s，2 秒样本 ≥1 张）
    assert row["keyframe_count"] >= 1
    # 分片数 = 结果章节数（内容分片），不虚构
    assert row["chapter_count"] == len(MockAnalysisLLM.MOCK_CHAPTERS)
    assert row["task_count"] == 1
    assert row["latest_task_id"] is not None
    assert row["latest_task_status"] == "success"


def test_videos_list_newest_first(client: TestClient) -> None:
    first = _upload(client)
    second = _upload(client)
    rows = client.get("/videos").json()
    assert [r["id"] for r in rows] == [second, first]


# ---------------------------------------------------------------- 任务列表


def test_tasks_list_newest_first_with_filename(client: TestClient) -> None:
    assert client.get("/tasks").json() == []

    video_id = _upload(client)
    first = _analyze(client, video_id)
    second = _analyze(client, video_id)

    rows = client.get("/tasks").json()
    assert [r["task_id"] for r in rows] == [second, first]  # 新→旧
    row = rows[0]
    assert set(row) >= {
        "task_id",
        "video_id",
        "video_filename",
        "status",
        "created_at",
        "started_at",
        "finished_at",
        "error_message",
    }
    assert row["video_id"] == video_id
    assert row["video_filename"].endswith(".mp4")
    assert row["status"] == "success"
    assert row["error_message"] is None
    assert row["finished_at"] is not None


# ---------------------------------------------------------------- 日志


def test_logs_endpoint_shape_and_limit(client: TestClient) -> None:
    marker = "panel-log-marker-basic"
    logging.getLogger("tests.panel").info(marker)

    resp = client.get("/logs", params={"limit": 5})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"items", "total"}
    assert 0 < len(body["items"]) <= 5
    assert body["total"] >= len(body["items"])
    line = body["items"][-1]
    assert set(line) == {"ts", "level", "logger", "message"}
    assert marker in client.get("/logs").text  # 默认 limit 200 内能取到


def test_logs_level_filter_keeps_threshold(client: TestClient) -> None:
    logging.getLogger("tests.panel").warning("panel-log-marker-warning")
    logging.getLogger("tests.panel").error("panel-log-marker-error")

    resp = client.get("/logs", params={"level": "ERROR"})
    assert resp.status_code == 200, resp.text
    levels = [item["level"] for item in resp.json()["items"]]
    assert set(levels) <= {"ERROR", "CRITICAL"}
    assert any("panel-log-marker-error" in item["message"] for item in resp.json()["items"])
    assert not any(
        "panel-log-marker-warning" in item["message"] for item in resp.json()["items"]
    )


def test_logs_invalid_params_return_422(client: TestClient) -> None:
    assert client.get("/logs", params={"limit": 0}).status_code == 422
    assert client.get("/logs", params={"limit": 1001}).status_code == 422
    assert client.get("/logs", params={"level": "BOGUS"}).status_code == 422


def test_logs_include_task_flow(client: TestClient) -> None:
    """分析流程日志（任务启动/完成）出现在缓冲里——面板「日志流程」的意义。"""
    video_id = _upload(client)
    task_id = _analyze(client, video_id)

    text = client.get("/logs", params={"limit": 1000}).text
    assert "任务" in text
    assert str(task_id) in text


# ---------------------------------------------------------------- 配置快照


def test_settings_snapshot_shape(client: TestClient) -> None:
    resp = client.get("/settings")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["app"]["title"]
    assert body["app"]["version"]
    assert set(body["providers"]) == {"transcription", "analysis", "agent", "driver"}
    assert set(body["models"]) == {"asr", "analysis", "agent"}
    assert set(body["mimo"]) == {"base_url", "api_key_configured"}
    assert set(body["limits"]) == {"max_upload_size_mb", "allowed_extensions"}
    assert body["limits"]["max_upload_size_mb"] == 500
    assert ".mp4" in body["limits"]["allowed_extensions"]
    assert body["keyframe_interval_seconds_default"] > 0
    assert isinstance(body["ffmpeg_available"], bool)


def test_settings_reports_key_configured_but_never_leaks_it(
    client: TestClient, monkeypatch
) -> None:
    from app import config

    secret = "sk-secret-xyz-do-not-leak"
    monkeypatch.setattr(config, "MIMO_API_KEY", secret)

    resp = client.get("/settings")

    assert resp.status_code == 200
    assert resp.json()["mimo"]["api_key_configured"] is True
    assert secret not in resp.text  # Key 明文绝不出现


def test_settings_reports_key_missing(client: TestClient, monkeypatch) -> None:
    from app import config

    monkeypatch.setattr(config, "MIMO_API_KEY", "")
    resp = client.get("/settings")
    assert resp.json()["mimo"]["api_key_configured"] is False


def test_settings_reflects_mock_providers_in_tests(client: TestClient) -> None:
    """conftest 切 mock 后快照应反映当前运行配置（读取请求时刻的 config）。"""
    body = client.get("/settings").json()
    assert body["providers"]["transcription"] == "mock"
    assert body["providers"]["analysis"] == "mock"
    assert body["providers"]["agent"] == "mock"


def test_transcript_mock_constant_still_used() -> None:
    """守住面板结果区展示的数据源：mock 转写文本与章节常量存在。"""
    assert MockTranscriptionService.MOCK_TEXT
    assert MockAnalysisLLM.MOCK_CHAPTERS
