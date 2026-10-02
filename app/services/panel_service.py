"""控制面板聚合服务（阶段十六）：只读地为面板端点准备数据。

- `list_video_summaries(session)`：视频列表 + 关键帧数（磁盘实数）+ 章节数（内容分片）
  + 最近任务状态，供 `GET /videos`。
- `list_task_summaries(session)`：任务列表（新→旧）+ 视频文件名，供 `GET /tasks`。
- `settings_snapshot()`：当前 provider/model/限额等配置快照（**不回传 API Key**），
  供 `GET /settings`。

约定：只读，不写库、不改配置；文件系统计数失败按 0 兜底（面板不因单个
视频的磁盘异常而 500）。
"""

from __future__ import annotations

import inspect
import json
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app import config
from app.database.repository import (
    ResultRepository,
    TaskRepository,
    VideoRepository,
)
from app.services import key_service
from app.services.keyframe_service import extract_keyframes


def count_keyframes(video_id: int | str) -> int:
    """统计 `data/outputs/{video_id}/frames/frame_*.jpg` 的实际帧数。"""
    try:
        frames_dir = Path(config.OUTPUTS_DIR) / str(video_id) / "frames"
        if not frames_dir.is_dir():
            return 0
        return sum(1 for _ in frames_dir.glob("frame_*.jpg"))
    except OSError:
        return 0


def _loads(raw: str | None) -> Any:
    """库内 JSON 字符串还原；空/非法一律返回 None（面板按 0 兜底）。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def list_video_summaries(session: Session) -> list[dict[str, Any]]:
    """每个视频一行概览（新→旧，面板表格直接渲染）。"""
    videos = VideoRepository(session).list_all()
    tasks = TaskRepository(session).list_all()  # 按 id 升序

    latest_task: dict[int, Any] = {}
    task_count: dict[int, int] = {}
    for task in tasks:
        latest_task[task.video_id] = task
        task_count[task.video_id] = task_count.get(task.video_id, 0) + 1

    summaries: list[dict[str, Any]] = []
    for video in reversed(videos):  # 新视频排前面
        task = latest_task.get(video.id)
        chapter_count = 0
        if task is not None and task.status == "success":
            result = ResultRepository(session).get_by_task(task.id)
            chapters = _loads(result.chapters) if result is not None else None
            if isinstance(chapters, list):
                chapter_count = len(chapters)
        summaries.append(
            {
                "id": video.id,
                "filename": video.filename,
                "duration": video.duration,
                "width": video.width,
                "height": video.height,
                "fps": video.fps,
                "created_at": video.created_at,
                "keyframe_count": count_keyframes(video.id),
                "chapter_count": chapter_count,
                "task_count": task_count.get(video.id, 0),
                "latest_task_id": task.id if task is not None else None,
                "latest_task_status": task.status if task is not None else None,
            }
        )
    return summaries


def list_task_summaries(session: Session) -> list[dict[str, Any]]:
    """任务列表（新→旧），带视频文件名，面板据此轮询状态与查结果。"""
    filenames = {v.id: v.filename for v in VideoRepository(session).list_all()}
    tasks = TaskRepository(session).list_all()
    return [
        {
            "task_id": task.id,
            "video_id": task.video_id,
            "video_filename": filenames.get(task.video_id, f"#{task.video_id}"),
            "status": task.status,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "error_message": task.error_message,
        }
        for task in reversed(tasks)
    ]


def _ffmpeg_available(name: str) -> bool:
    """可执行文件是否可用：优先 config.FFMPEG_DIR，其次 PATH。"""
    if config.FFMPEG_DIR:
        base = Path(config.FFMPEG_DIR)
        if (base / f"{name}.exe").is_file() or (base / name).is_file():
            return True
    return shutil.which(name) is not None


def settings_snapshot() -> dict[str, Any]:
    """当前运行配置快照；只暴露「是否已配置 Key + 掩码 + 来源」，绝不回传 Key 本身。"""
    interval = inspect.signature(extract_keyframes).parameters["interval_seconds"]
    key_status = key_service.status()
    return {
        "app": {"title": config.APP_TITLE, "version": config.APP_VERSION},
        "providers": {
            "transcription": config.TRANSCRIPTION_PROVIDER,
            "analysis": config.ANALYSIS_PROVIDER,
            "agent": config.AGENT_PROVIDER,
            "driver": config.AGENT_DRIVER,
        },
        "models": {
            "asr": config.MIMO_ASR_MODEL,
            "analysis": config.MIMO_ANALYSIS_MODEL,
            "agent": config.MIMO_AGENT_MODEL,
        },
        "mimo": {
            "base_url": config.MIMO_BASE_URL,
            # Key 只回传「是否已配置 + 掩码 + 来源」，绝不回传真值（见 key_service）
            "api_key_configured": key_status["configured"],
            "api_key_masked": key_status["masked"],
            "api_key_source": key_status["source"],
        },
        "limits": {
            "max_upload_size_mb": config.MAX_UPLOAD_SIZE_MB,
            "allowed_extensions": sorted(config.ALLOWED_VIDEO_EXTENSIONS),
        },
        "keyframe_interval_seconds_default": interval.default,
        "ffmpeg_available": _ffmpeg_available("ffmpeg"),
        "ffprobe_available": _ffmpeg_available("ffprobe"),
    }
