"""阶段二测试：视频信息解析（ffprobe）。

- 正常视频：`sample_video` fixture（见 conftest.py）由 ffmpeg 现场生成。
- 异常路径：文件不存在 / 非视频文件 / ffprobe 缺失。
"""

import shutil
from pathlib import Path

import pytest

from app.services.video_service import (
    FFmpegNotFoundError,
    InvalidVideoError,
    VideoNotFoundError,
    get_video_info,
)


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


def test_ffmpeg_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 用临时占位文件，确保"文件存在"先于 ffprobe 解析这一步
    placeholder = tmp_path / "any.mp4"
    placeholder.write_bytes(b"placeholder")
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    with pytest.raises(FFmpegNotFoundError):
        get_video_info(placeholder)
