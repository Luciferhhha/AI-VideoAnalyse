"""阶段六测试：音频提取（FFmpeg）。

- 正常视频：`sample_video`（conftest.py，h264+aac）→ 真实 FFmpeg 提取 wav。
- 异常路径：无音频视频 / 文件不存在 / ffmpeg 缺失 / ffmpeg 提取失败（半成品清理）。
- 冲突命名：同一 video_id 重复提取自动编号，不覆盖。
"""

import subprocess
import wave
from pathlib import Path

import pytest

from app import config
from app.services.audio_service import (
    AudioExtractionError,
    NoAudioStreamError,
    extract_audio,
)
from app.services.video_service import FFmpegNotFoundError, VideoNotFoundError


def test_extract_audio_normal(sample_video: Path, tmp_path: Path) -> None:
    out = extract_audio(sample_video, video_id=1, output_root=tmp_path)

    assert out == tmp_path / "1" / "audio.wav"
    assert out.exists() and out.stat().st_size > 0
    with wave.open(str(out)) as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 16000
        assert wf.getsampwidth() == 2  # pcm_s16le
        duration = wf.getnframes() / wf.getframerate()
        assert abs(duration - 2.0) < 0.5


def test_extract_audio_avoids_name_conflict(sample_video: Path, tmp_path: Path) -> None:
    first = extract_audio(sample_video, video_id=2, output_root=tmp_path)
    second = extract_audio(sample_video, video_id=2, output_root=tmp_path)
    third = extract_audio(sample_video, video_id=2, output_root=tmp_path)

    assert first.name == "audio.wav"
    assert second.name == "audio_1.wav"
    assert third.name == "audio_2.wav"
    assert first.exists() and second.exists() and third.exists()


def test_extract_audio_no_audio_stream(no_audio_video: Path, tmp_path: Path) -> None:
    with pytest.raises(NoAudioStreamError) as exc:
        extract_audio(no_audio_video, video_id=7, output_root=tmp_path)
    assert "没有音频流" in str(exc.value)
    # 失败时不残留输出目录/半成品
    assert not (tmp_path / "7").exists()


def test_extract_audio_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(VideoNotFoundError):
        extract_audio(tmp_path / "no_such.mp4", video_id=8, output_root=tmp_path)


def test_extract_audio_ffmpeg_missing(sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 让 audio_service 的解析器找不到 ffmpeg（阶段二的 ffprobe 走 PATH 不受影响）
    monkeypatch.setattr(config, "FFMPEG_DIR", str(tmp_path / "no_such_ffmpeg_dir"))
    with pytest.raises(FFmpegNotFoundError) as exc:
        extract_audio(sample_video, video_id=3, output_root=tmp_path)
    message = str(exc.value)
    assert "ffmpeg" in message and "ffprobe" not in message


def test_extract_audio_ffmpeg_failure_cleans_partial(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ffmpeg 失败（returncode != 0）：抛 AudioExtractionError，半成品 wav 被清理。"""
    import app.services.audio_service as audio_service

    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if "ffprobe" in Path(cmd[0]).name:
            return real_run(cmd, **kwargs)
        Path(cmd[-1]).write_bytes(b"partial")  # 模拟写了一半的输出
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="boom: unknown codec (fake failure)"
        )

    monkeypatch.setattr(audio_service.subprocess, "run", fake_run)

    with pytest.raises(AudioExtractionError) as exc:
        extract_audio(sample_video, video_id=4, output_root=tmp_path)
    assert "fake failure" in str(exc.value)
    assert not list((tmp_path / "4").glob("*.wav"))
