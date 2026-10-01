"""阶段八：关键帧提取测试（正常视频 / 短视频 / 文件不存在 + 边界）。"""

from pathlib import Path

import pytest

from app.services.keyframe_service import (
    KeyframeServiceError,
    extract_keyframes,
)
from app.services.video_service import InvalidVideoError, VideoNotFoundError


def test_extract_keyframes_normal(sample_video: Path, tmp_path: Path) -> None:
    """正常视频：2 秒、1 秒间隔 → 抽 t=0 与 t=1 两帧，编号与内容有效。"""
    records = extract_keyframes(
        sample_video, 7, interval_seconds=1.0, output_root=tmp_path
    )

    # t=2.0 等于视频时长，不取（t < duration）
    assert [r["timestamp"] for r in records] == [0.0, 1.0]
    assert [Path(r["filepath"]).name for r in records] == [
        "frame_0001.jpg",
        "frame_0002.jpg",
    ]
    assert records[0]["filepath"] == str(
        tmp_path / "7" / "frames" / "frame_0001.jpg"
    )
    for r in records:
        p = Path(r["filepath"])
        assert p.is_file()
        assert p.stat().st_size > 0
        assert p.read_bytes()[:2] == b"\xff\xd8"  # JPEG SOI 魔数


def test_extract_keyframes_short_video(sample_video: Path, tmp_path: Path) -> None:
    """短视频：2 秒 < 默认 30 秒间隔 → 至少抽第 0 秒 1 帧。"""
    records = extract_keyframes(sample_video, "v1", output_root=tmp_path)

    assert len(records) == 1
    assert records[0]["timestamp"] == 0.0
    assert Path(records[0]["filepath"]).is_file()


def test_extract_keyframes_file_not_found(tmp_path: Path) -> None:
    """文件不存在 → 复用阶段二 VideoNotFoundError 语义。"""
    with pytest.raises(VideoNotFoundError):
        extract_keyframes(tmp_path / "ghost.mp4", 1, output_root=tmp_path)


def test_extract_keyframes_non_video(tmp_path: Path) -> None:
    """非视频文件 → 复用阶段二 InvalidVideoError 语义。"""
    fake = tmp_path / "fake.mp4"
    fake.write_text("not a video", encoding="utf-8")
    with pytest.raises(InvalidVideoError):
        extract_keyframes(fake, 1, output_root=tmp_path)


def test_extract_keyframes_invalid_interval(
    sample_video: Path, tmp_path: Path
) -> None:
    """间隔必须为正数。"""
    with pytest.raises(KeyframeServiceError, match="间隔"):
        extract_keyframes(sample_video, 1, interval_seconds=0, output_root=tmp_path)


def test_extract_keyframes_illegal_video_id(
    sample_video: Path, tmp_path: Path
) -> None:
    """video_id 含路径分隔符 → 拒绝（防路径穿越）。"""
    with pytest.raises(KeyframeServiceError, match="video_id"):
        extract_keyframes(sample_video, "../evil", output_root=tmp_path)


def test_extract_keyframes_rerun_clears_stale_frames(
    sample_video: Path, tmp_path: Path
) -> None:
    """重复提取先清旧帧：编号始终对应本次结果，不残留多余帧。"""
    first = extract_keyframes(
        sample_video, 9, interval_seconds=1.0, output_root=tmp_path
    )
    assert len(first) == 2

    second = extract_keyframes(
        sample_video, 9, interval_seconds=60.0, output_root=tmp_path
    )
    assert len(second) == 1
    frames = sorted(p.name for p in (tmp_path / "9" / "frames").glob("*.jpg"))
    assert frames == ["frame_0001.jpg"]


def test_extract_keyframes_unicode_output_path(
    sample_video: Path, tmp_path: Path
) -> None:
    """输出根含中文路径也能写帧（cv2.imwrite 窄字符 bug 的回归防线）。"""
    unicode_root = tmp_path / "视频输出"
    records = extract_keyframes(sample_video, 3, output_root=unicode_root)
    assert len(records) == 1
    p = Path(records[0]["filepath"])
    assert p.is_file()
    assert p.stat().st_size > 0
    assert "视频输出" in str(p)
