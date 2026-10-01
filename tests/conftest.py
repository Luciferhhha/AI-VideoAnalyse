"""基础测试配置：保证从项目根目录可导入 app 包 + 共享测试视频与客户端 fixture。"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """隔离客户端：上传/输出目录重定向到 tmp + 临时 SQLite 库（dependency_overrides）。

    阶段四（上传）、阶段五（异步任务）、阶段七（转写接入占位链路）共用；
    后台任务经 `db.get_bind()` 拿到同一个临时引擎，因此也不会污染真实
    data/ 与 data/database。转写默认走 mock（无需 API Key，7.5）。
    """
    from app import config
    from app.database.database import Base, create_db_engine, get_db
    from app.main import app

    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "TRANSCRIPTION_PROVIDER", "mock")
    engine = create_db_engine(f"sqlite:///{tmp_path.as_posix()}/test_api.db")
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    def _override_get_db():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture(scope="session")
def no_audio_video() -> Path:
    """生成（或复用）无音频视频：2 秒，320x240，10fps，仅视频流（阶段六用）。"""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    out = FIXTURES_DIR / "no_audio.mp4"
    if out.exists():
        return out
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg, "需要 PATH 中的 ffmpeg 来生成测试视频"
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(out),
    ]
    kwargs = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(cmd, **kwargs)
    assert result.returncode == 0, f"生成无音频测试视频失败: {result.stderr[-500:]}"
    return out


@pytest.fixture(scope="session")
def sample_video() -> Path:
    """生成（或复用）最小测试视频：2 秒，320x240，10fps，h264+aac。"""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    out = FIXTURES_DIR / "sample.mp4"
    if out.exists():
        return out
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg, "需要 PATH 中的 ffmpeg 来生成测试视频"
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(out),
    ]
    kwargs = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(cmd, **kwargs)
    assert result.returncode == 0, f"生成测试视频失败: {result.stderr[-500:]}"
    return out
