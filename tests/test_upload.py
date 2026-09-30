"""阶段四测试：视频上传与查询 API。

- 测试隔离：共用 `tests/conftest.py` 的 `client` fixture（临时 SQLite 库 +
  上传目录 monkeypatch 到 tmp），不污染真实 data/uploads 与 data/database。
- 覆盖：正常上传（201 + 返回体 + 文件落盘 + 记录可查）、非法扩展名 400、
  伪装成 mp4 的非视频内容 400（且残留文件被清理）、超大文件 413、视频不存在 404。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from tests.conftest import FIXTURES_DIR


def _upload(client: TestClient, name: str, content: bytes | None = None, path: Path | None = None):
    if content is None:
        content = (path or (FIXTURES_DIR / "sample.mp4")).read_bytes()
    return client.post("/videos", files={"file": (name, content, "application/octet-stream")})


def test_upload_valid_video(client: TestClient, tmp_path: Path) -> None:
    resp = _upload(client, "sample.mp4")

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == {"id", "filename"}
    assert body["id"] == 1
    assert body["filename"] == "sample.mp4"

    # 文件确实落盘（在被重定向的临时上传目录里）
    saved = list((tmp_path / "uploads").glob("*_sample.mp4"))
    assert len(saved) == 1
    assert saved[0].stat().st_size > 0

    # 数据库记录带上了 ffprobe 元数据
    info = client.get("/videos/1")
    assert info.status_code == 200
    detail = info.json()
    assert detail["filename"] == "sample.mp4"
    assert detail["duration"] is not None and abs(detail["duration"] - 2.0) < 0.5
    assert detail["width"] == 320 and detail["height"] == 240
    assert detail["fps"] == 10.0
    assert detail["created_at"] is not None


def test_upload_rejects_bad_extension(client: TestClient, tmp_path: Path) -> None:
    resp = _upload(client, "note.txt", content=b"just text")
    assert resp.status_code == 400
    assert "不支持的文件类型" in resp.json()["detail"]
    # 没有文件落盘、没有记录
    assert not list((tmp_path / "uploads").glob("*"))
    assert client.get("/videos/1").status_code == 404


def test_upload_rejects_fake_video(client: TestClient, tmp_path: Path) -> None:
    # 扩展名合法但内容不是视频 → ffprobe 解析失败 → 400，且残留文件被删除
    resp = _upload(client, "fake.mp4", content=b"this is definitely not a video file")
    assert resp.status_code == 400
    assert resp.json()["detail"]
    assert not list((tmp_path / "uploads").glob("*"))
    assert client.get("/videos/1").status_code == 404


def test_upload_rejects_too_large(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAX_UPLOAD_SIZE_MB", 0)  # 0 MB → 任何文件都超限
    resp = _upload(client, "sample.mp4")
    assert resp.status_code == 413
    assert "超过大小限制" in resp.json()["detail"]
    assert not list((tmp_path / "uploads").glob("*"))


def test_get_missing_video_returns_404(client: TestClient) -> None:
    resp = client.get("/videos/999")
    assert resp.status_code == 404
    assert "999" in resp.json()["detail"]
