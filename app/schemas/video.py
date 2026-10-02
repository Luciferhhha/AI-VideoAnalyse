"""视频相关 API 的出入参模型（阶段四）。"""

from datetime import datetime

from pydantic import BaseModel


class VideoCreateResponse(BaseModel):
    """POST /videos 成功响应，对应计划书示例 {"id", "filename"}。"""

    id: int
    filename: str


class VideoDetailResponse(BaseModel):
    """GET /videos/{video_id} 响应：视频信息。"""

    id: int
    filename: str
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    created_at: datetime


class VideoListResponse(BaseModel):
    """GET /videos 响应：控制面板用的视频概览行（含关键帧数与章节数）。"""

    id: int
    filename: str
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    created_at: datetime
    keyframe_count: int = 0  # data/outputs/{id}/frames/frame_*.jpg 实际帧数
    chapter_count: int = 0  # 最近成功任务结果里的章节数（内容分片）
    task_count: int = 0
    latest_task_id: int | None = None
    latest_task_status: str | None = None
