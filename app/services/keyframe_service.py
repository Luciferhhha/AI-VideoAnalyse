"""关键帧提取服务：用 OpenCV 按固定时间间隔抽帧。

职责（阶段八）：
- `extract_keyframes(video_path, video_id, *, interval_seconds=5.0)`：默认每
  30 秒抽 1 帧，输出 `{output_root}/{video_id}/frames/frame_0001.jpg…`（默认
  `config.OUTPUTS_DIR`，即 `data/outputs/{video_id}/frames/`），每帧返回
  `{"timestamp": 秒, "filepath": 路径}` 记录。
- 复用阶段二 `get_video_info` 做前置校验（文件存在 / 有效视频），
  异常同义复用 `VideoNotFoundError` / `InvalidVideoError`；
  新增 `KeyframeServiceError`（参数/目录非法）与
  `KeyframeExtractionError`（打开/读帧/写帧失败）。
- 重复提取同一 video_id 前先清理旧的 `frame_*.jpg`，保证
  `frame_0001.jpg…` 编号与本次一致，不留残留帧。
- 短视频（时长 < 间隔）至少抽出第 0 秒 1 帧。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import cv2

from app import config
from app.services.video_service import (
    get_video_info,
)

logger = logging.getLogger(__name__)


class KeyframeServiceError(Exception):
    """关键帧提取错误基类。"""


class KeyframeExtractionError(KeyframeServiceError):
    """OpenCV 打开视频 / 读帧 / 写帧失败。"""


def _safe_video_id(video_id: int | str) -> str:
    """校验 video_id 可安全作为目录名（防止路径穿越），返回目录名。"""
    key = str(video_id)
    if not key or key in (".", "..") or "/" in key or "\\" in key:
        raise KeyframeServiceError(f"非法 video_id，不能作为输出目录名：{video_id!r}")
    return key


def _frame_timestamps(duration: float, interval_seconds: float) -> list[float]:
    """生成抽帧时间点：0, interval, 2*interval… 且 t < duration。

    - 第 0 秒恒为抽帧点（短视频因此至少 1 帧）；
    - duration <= 0（ffprobe 未给出时长）时只抽第 0 秒。
    """
    timestamps = [0.0]
    t = interval_seconds
    while t < duration:
        timestamps.append(round(t, 3))
        t += interval_seconds
    return timestamps


def extract_keyframes(
    video_path: str | Path,
    video_id: int | str,
    *,
    interval_seconds: float = 5.0,
    output_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """按固定间隔抽帧，返回记录列表 `[{"timestamp": float, "filepath": str}]`。

    - 输出位置：`{output_root}/{video_id}/frames/frame_0001.jpg…`，
      `output_root` 缺省为 `config.OUTPUTS_DIR`。
    - 时间点：0s 起每隔 `interval_seconds` 秒一帧，取 t < 时长；
      短视频至少抽第 0 秒 1 帧。
    - 重名处理：提取前清空该目录既有 `frame_*.jpg`（编号确定性）。

    异常：
    - VideoNotFoundError：视频文件不存在（复用阶段二语义）
    - InvalidVideoError：非视频文件 / 无视频流（复用阶段二语义）
    - KeyframeServiceError：interval 非正数 / 非法 video_id
    - KeyframeExtractionError：OpenCV 打不开 / 读不出帧 / 写不了 jpg
    """
    if interval_seconds <= 0:
        raise KeyframeServiceError(f"抽帧间隔必须为正数：{interval_seconds}")

    # 先走阶段二的校验：存在性 → 有效视频
    info = get_video_info(video_path)
    duration = float(info["duration"] or 0.0)

    root = Path(output_root) if output_root is not None else config.OUTPUTS_DIR
    frames_dir = root / _safe_video_id(video_id) / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for stale in frames_dir.glob("frame_*.jpg"):
        stale.unlink(missing_ok=True)

    timestamps = _frame_timestamps(duration, interval_seconds)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise KeyframeExtractionError(
            f"OpenCV 无法打开视频：{Path(video_path).name}"
        )

    records: list[dict[str, Any]] = []
    try:
        for idx, ts in enumerate(timestamps, start=1):
            cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                if idx == 1:
                    raise KeyframeExtractionError(
                        f"读取视频帧失败：{Path(video_path).name}（t={ts:g}s）"
                    )
                # 末尾 seek 超出实际范围等情况：以已抽到的帧为准
                break
            out_path = frames_dir / f"frame_{idx:04d}.jpg"
            # cv2.imwrite 走窄字符 fopen，中文路径（如 H:\视频分析工程）会失败；
            # 改为 imencode 编码 + Python 写盘，路径 Unicode 安全。
            ok_enc, buf = cv2.imencode(".jpg", frame)
            if not ok_enc:
                raise KeyframeExtractionError(
                    f"JPEG 编码失败：{Path(video_path).name}（t={ts:g}s）"
                )
            try:
                out_path.write_bytes(buf.tobytes())
            except OSError as exc:
                raise KeyframeExtractionError(f"写入关键帧失败：{out_path} —— {exc}") from exc
            records.append({"timestamp": ts, "filepath": str(out_path)})
    finally:
        cap.release()

    logger.info(
        "关键帧提取完成 video_id=%s → %d 帧（间隔 %g 秒，时长 %g 秒）",
        video_id, len(records), interval_seconds, duration,
    )
    return records
