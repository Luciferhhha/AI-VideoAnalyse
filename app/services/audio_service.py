"""音频提取服务：通过 FFmpeg 把视频音轨提取为 wav。

职责（阶段六）：
- `extract_audio(video_path, video_id)`：提取音轨为 16kHz 单声道 PCM wav，
  输出到 `{output_root}/{video_id}/audio.wav`（默认 `config.OUTPUTS_DIR`，
  即 `data/outputs/{video_id}/`），同名冲突时自动编号 `audio_1.wav`…
- 复用阶段二 `get_video_info` 做前置校验（文件存在 / 有效视频 / 有音轨），
  异常同义复用 `VideoNotFoundError` / `InvalidVideoError` / `FFmpegNotFoundError`，
  新增 `NoAudioStreamError`（无音轨）与 `AudioExtractionError`（ffmpeg 失败/超时）。
- 输出规格 16kHz 单声道 PCM s16le：阶段七 mimo 语音转文字的输入规格。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

from app import config
from app.services.video_service import (
    FFmpegNotFoundError,
    get_video_info,
)

logger = logging.getLogger(__name__)


class AudioServiceError(Exception):
    """音频提取错误基类。"""


class NoAudioStreamError(AudioServiceError):
    """视频中没有音频流，无法提取音轨。"""


class AudioExtractionError(AudioServiceError):
    """FFmpeg 音频提取失败或超时。"""


def _resolve_ffmpeg() -> str:
    """定位 ffmpeg：优先 config.FFMPEG_DIR，其次 PATH。"""
    if config.FFMPEG_DIR:
        base = Path(config.FFMPEG_DIR)
        for name in ("ffmpeg.exe", "ffmpeg"):
            candidate = base / name
            if candidate.exists():
                return str(candidate)
        raise FFmpegNotFoundError(
            f"在 FFMPEG_DIR={config.FFMPEG_DIR!r} 下未找到 ffmpeg，请检查 app/config.py 的 FFMPEG_DIR 设置。"
        )
    path = shutil.which("ffmpeg")
    if not path:
        raise FFmpegNotFoundError(
            "未找到 ffmpeg：请安装 FFmpeg 并把 bin 目录加入 PATH，"
            "或在 app/config.py 中设置 FFMPEG_DIR 指向 bin 目录。"
        )
    return path


def _safe_video_id(video_id: int | str) -> str:
    """校验 video_id 可安全作为目录名（防止路径穿越），返回目录名。"""
    key = str(video_id)
    if not key or key in (".", "..") or "/" in key or "\\" in key:
        raise AudioServiceError(f"非法 video_id，不能作为输出目录名：{video_id!r}")
    return key


def _unique_wav_path(directory: Path, stem: str = "audio") -> Path:
    """生成不冲突的输出路径：audio.wav → audio_1.wav → audio_2.wav…"""
    candidate = directory / f"{stem}.wav"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{counter}.wav"
        counter += 1
    return candidate


def extract_audio(
    video_path: str | Path,
    video_id: int | str,
    *,
    output_root: str | Path | None = None,
) -> Path:
    """提取视频音轨为 wav，返回输出文件路径。

    - 输出位置：`{output_root}/{video_id}/audio.wav`，`output_root` 缺省为
      `config.OUTPUTS_DIR`（即 `data/outputs/{video_id}/audio.wav`）。
    - 重复提取同一 video_id 时自动编号 `audio_1.wav`…，避免互相覆盖。
    - wav 规格：PCM s16le、16000 Hz、单声道（阶段七语音转文字输入规格）。

    异常：
    - VideoNotFoundError：视频文件不存在（复用阶段二语义）
    - InvalidVideoError：非视频文件 / 无视频流（复用阶段二语义）
    - FFmpegNotFoundError：找不到 ffmpeg
    - NoAudioStreamError：视频没有音频流
    - AudioExtractionError：FFmpeg 提取失败 / 超时 / 未产出有效 wav
    - AudioServiceError：非法 video_id
    """
    # 先走阶段二的校验：存在性 → 有效视频 → 有音轨
    info = get_video_info(video_path)
    if info["audio_codec"] is None:
        raise NoAudioStreamError(f"视频没有音频流，无法提取音轨：{Path(video_path).name}")

    ffmpeg = _resolve_ffmpeg()

    root = Path(output_root) if output_root is not None else config.OUTPUTS_DIR
    out_dir = root / _safe_video_id(video_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = _unique_wav_path(out_dir)

    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-i", str(video_path),
        "-vn",                    # 只要音轨，不要视频
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(out_path),
    ]
    # 超时：基准 60 秒 + 按视频时长预留（提取速度远快于实时，这里给足余量）
    timeout = 60 + 2 * int(info["duration"] or 0)
    kwargs: dict = {
        "capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace",
        "timeout": timeout,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        result = subprocess.run(cmd, **kwargs)
    except subprocess.TimeoutExpired as exc:
        out_path.unlink(missing_ok=True)
        raise AudioExtractionError(
            f"提取音频超时（FFmpeg {timeout} 秒无响应）：{Path(video_path).name}"
        ) from exc
    except OSError as exc:  # ffmpeg 可执行文件无法启动等
        out_path.unlink(missing_ok=True)
        raise AudioExtractionError(f"无法启动 FFmpeg：{exc}") from exc

    if result.returncode != 0:
        out_path.unlink(missing_ok=True)  # 清掉半成品
        stderr_tail = (result.stderr or "").strip().splitlines()[-1:] or ["无错误输出"]
        logger.warning(
            "ffmpeg 音频提取失败（exit=%s）：%s", result.returncode, stderr_tail[0]
        )
        raise AudioExtractionError(
            f"提取音频失败：{Path(video_path).name} —— ffmpeg: {stderr_tail[0]}"
        )

    size = out_path.stat().st_size if out_path.exists() else 0
    if size <= 0:
        out_path.unlink(missing_ok=True)
        raise AudioExtractionError(
            f"提取音频失败：FFmpeg 未生成有效 wav —— {Path(video_path).name}"
        )

    logger.info("音频提取完成 video_id=%s → %s（%d bytes）", video_id, out_path, size)
    return out_path
