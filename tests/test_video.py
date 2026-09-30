"""阶段二测试：视频信息解析（ffprobe）。

- 正常视频：由 ffmpeg 现场生成 tests/fixtures/sample.mp4（2 秒，320x240，10fps，h264+aac）。
- 异常路径：文件不存在 / 非视频文件 / ffprobe 缺失。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.video_service import (
    FFmpegNotFoundError,
    InvalidVideoError,
    VideoNotFoundError,
    get_video_info,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def sample_video() -> Path:
    """生成（或复用）最小测试视频。"""
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
    kwargs = {"capture_output": True, "text": True}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(cmd, **kwargs)
    assert result.returncode == 0, f"生成测试视频失败: {result.stderr[-500:]}"
    return out


def test_get_video_info_normal(sample_video: Path) -> None:
    info = get_video_info(sample_video)

    assert info["filename"] == "sample.mp4"
    assert info["size"] > 0
    assert info["duration"] is not None and abs(info["duration"] - 2.0) < 0.5
    assert info["width"] == 320
    assert info["height"] == 240
    assert info["fps"] == 10.0
    assert info["video_codec"] == "h264"
    assert info["audio_codec"] == "aac"


def test_video_not_found(tmp_path: Path) -> None:
    with pytest.raises(VideoNotFoundError):
        get_video_info(tmp_path / "no_such_video.mp4")


def test_non_video_file(tmp_path: Path) -> None:
    fake = tmp_path / "not_a_video.txt"
    fake.write_text("hello, I am not a video", encoding="utf-8")
    with pytest.raises(InvalidVideoError):
        get_video_info(fake)


def test_ffmpeg_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    with pytest.raises(FFmpegNotFoundError):
        get_video_info(FIXTURES_DIR / "sample.mp4")
