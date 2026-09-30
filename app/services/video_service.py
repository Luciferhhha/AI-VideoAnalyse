"""视频信息解析服务：通过 ffprobe 提取视频元数据。

职责（阶段二）：
- `get_video_info(video_path)`：返回文件名、大小、时长、宽、高、FPS、视频/音频编码。
- 异常分层：文件不存在 / 非视频文件 / ffprobe 缺失，各自给出明确错误文案。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

from app.config import FFMPEG_DIR


class VideoServiceError(Exception):
    """视频服务错误基类。"""


class VideoNotFoundError(VideoServiceError):
    """视频文件不存在。"""


class InvalidVideoError(VideoServiceError):
    """文件不是有效视频（或 ffprobe 无法解析出视频流）。"""


class FFmpegNotFoundError(VideoServiceError):
    """系统中找不到 ffprobe 可执行文件。"""


def _resolve_ffprobe() -> str:
    """定位 ffprobe：优先 config.FFMPEG_DIR，其次 PATH。"""
    if FFMPEG_DIR:
        base = Path(FFMPEG_DIR)
        for name in ("ffprobe.exe", "ffprobe"):
            candidate = base / name
            if candidate.exists():
                return str(candidate)
        raise FFmpegNotFoundError(
            f"在 FFMPEG_DIR={FFMPEG_DIR!r} 下未找到 ffprobe，请检查 app/config.py 的 FFMPEG_DIR 设置。"
        )
    path = shutil.which("ffprobe")
    if not path:
        raise FFmpegNotFoundError(
            "未找到 ffprobe：请安装 FFmpeg 并把 bin 目录加入 PATH，"
            "或在 app/config.py 中设置 FFMPEG_DIR 指向 bin 目录。"
        )
    return path


def _run_ffprobe(ffprobe: str, path: Path) -> dict:
    """执行 ffprobe 并返回 JSON 结果；失败抛 InvalidVideoError。"""
    cmd = [
        ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    kwargs: dict = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(cmd, timeout=60, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise InvalidVideoError(f"解析视频超时（ffprobe 60 秒无响应）：{path.name}") from exc
    if result.returncode != 0:
        stderr_tail = (result.stderr or "").strip().splitlines()[-1:] or ["无错误输出"]
        raise InvalidVideoError(
            f"无法解析该文件（可能不是视频文件）：{path.name} —— ffprobe: {stderr_tail[0]}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise InvalidVideoError(f"ffprobe 输出不是合法 JSON：{path.name}") from exc


def _parse_fps(stream: dict) -> float | None:
    """把 '10/1' 这类帧率字符串解析成浮点数。"""
    for key in ("r_frame_rate", "avg_frame_rate"):
        raw = stream.get(key)
        if not raw or raw in ("0/0", "N/A"):
            continue
        try:
            frac = Fraction(raw)
        except (ValueError, ZeroDivisionError):
            continue
        if frac.denominator == 0 or frac == 0:
            continue
        return round(float(frac), 3)
    return None


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_video_info(video_path: str | Path) -> dict:
    """获取视频元数据。

    返回：{"filename", "size", "duration", "width", "height",
           "fps", "video_codec", "audio_codec"}

    异常：
    - VideoNotFoundError：文件不存在
    - InvalidVideoError：非视频文件 / 无视频流 / ffprobe 解析失败
    - FFmpegNotFoundError：找不到 ffprobe
    """
    path = Path(video_path)
    if not path.exists():
        raise VideoNotFoundError(f"视频文件不存在：{path}")
    if not path.is_file():
        raise InvalidVideoError(f"路径不是文件：{path}")

    ffprobe = _resolve_ffprobe()
    data = _run_ffprobe(ffprobe, path)

    streams = data.get("streams") or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video_stream is None:
        raise InvalidVideoError(f"文件中没有视频流：{path.name}")
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    fmt = data.get("format") or {}
    duration = _to_float(fmt.get("duration"))
    if duration is None:
        duration = _to_float(video_stream.get("duration"))

    size = _to_float(fmt.get("size"))
    if size is None:
        size = float(path.stat().st_size)

    return {
        "filename": path.name,
        "size": int(size),
        "duration": round(duration, 3) if duration is not None else None,
        "width": int(video_stream["width"]) if video_stream.get("width") else None,
        "height": int(video_stream["height"]) if video_stream.get("height") else None,
        "fps": _parse_fps(video_stream),
        "video_codec": video_stream.get("codec_name"),
        "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
    }
